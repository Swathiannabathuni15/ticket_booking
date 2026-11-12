"""Background jobs inside the web server process (BACKGROUND_JOBS=inprocess).

On a small or free host you usually get ONE process, so there is nowhere to run the
separate worker and scheduler. This loop does the same work: finish payments whose
webhook never came, cancel unpaid bookings, and send "starts soon" notifications.
A short Redis lock means that if several server processes run, only one does each
round. (With a real worker setup keep BACKGROUND_JOBS=off.)
"""
import logging
import threading
import time

import redis

from app.config import settings
from app.db import SessionLocal
from app.redis_client import get_redis
from app.services.payment_service import expire_unpaid_bookings, reconcile_pending_attempts
from app.services.push_service import send_due_reminders

log = logging.getLogger("jobs.inprocess")
LOCK_KEY = "bgjobs:lock"


def tick(r: redis.Redis | None = None) -> dict:
    """One round of all three jobs. Returns what happened (or {"skipped": True})."""
    r = r or get_redis()
    try:
        # Lock for slightly less than one interval: one winner per round across processes.
        if not r.set(LOCK_KEY, "1", nx=True, ex=max(1, settings.payment_sweep_interval_seconds - 5)):
            return {"skipped": True}
    except redis.exceptions.RedisError:
        return {"skipped": True}          # no Redis: do nothing rather than run unlocked
    out = {}
    with SessionLocal() as db:
        for name, fn in (("reconciled", reconcile_pending_attempts), ("expired", expire_unpaid_bookings),
                         ("reminders", send_due_reminders)):
            try:
                out[name] = fn(db)
            except Exception:             # one failing job must not stop the others
                db.rollback()
                log.exception("job %s failed", name)
                out[name] = "error"
    return out


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            log.exception("background round failed")
        time.sleep(settings.payment_sweep_interval_seconds)


def start() -> threading.Thread:
    t = threading.Thread(target=_loop, name="inprocess-jobs", daemon=True)
    t.start()
    log.info("In-process background jobs started (every %ss)", settings.payment_sweep_interval_seconds)
    return t
