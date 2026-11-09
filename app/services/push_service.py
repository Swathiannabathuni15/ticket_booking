"""Phone / desktop notifications with Web Push.

HOW IT WORKS
    1. The website registers a service worker (frontend/public/sw.js) and asks
       the browser for permission. The browser gives back a *subscription*: an
       address on its vendor's push server (Google, Mozilla, Apple) plus keys.
       We store it (push_subscriptions).
    2. Every ~30 s a background job (app/jobs) finds paid bookings whose show
       starts within `reminder_minutes`, and sends each owner's devices an
       encrypted message through that push server.
    3. The browser wakes the service worker and shows a normal system
       notification - even when the website is closed.
    Messages are signed with VAPID keys (ours) so push servers know who sent them.

WHAT THIS CANNOT DO
    - It needs HTTPS (localhost counts as secure, a laptop's LAN address does not),
      so a real phone only gets notifications once the site is served over HTTPS.
    - On iPhone it only works for a site added to the Home Screen (iOS 16.4+).
    - It is not SMS. Texting a phone number needs a paid SMS provider.
"""
import base64
import json
import logging
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, PushSubscription, ReminderSent, Showtime

log = logging.getLogger(__name__)
KEY_FILE = Path(__file__).resolve().parents[2] / ".vapid_keys.json"


class SubscriptionGone(Exception):
    """The push server says this device will never accept messages again."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keys() -> dict:
    v = Vapid()
    v.generate_keys()
    private = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    public = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"private": _b64(private), "public": _b64(public)}


def get_keys() -> dict:
    """Keys from settings, else from .vapid_keys.json (created on first use)."""
    if settings.vapid_private_key and settings.vapid_public_key:
        return {"private": settings.vapid_private_key, "public": settings.vapid_public_key}
    if KEY_FILE.exists():
        return json.loads(KEY_FILE.read_text())
    keys = generate_keys()
    KEY_FILE.write_text(json.dumps(keys))
    os.chmod(KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)  # private key: owner only
    log.info("Generated new VAPID keys in %s", KEY_FILE)
    return keys


def _real_send(sub: PushSubscription, payload: dict) -> None:
    keys = get_keys()
    try:
        webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload),
            vapid_private_key=Vapid.from_raw(keys["private"].encode()),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=int(settings.reminder_minutes * 60),
            timeout=10,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            raise SubscriptionGone() from exc
        raise


# Replaceable in tests (they must never contact a real push server).
_sender: Callable[[PushSubscription, dict], None] = _real_send


def set_sender(fn: Callable[[PushSubscription, dict], None]) -> None:
    global _sender
    _sender = fn


def directions_url(theatre: str, city: str) -> str:
    return "https://www.google.com/maps/dir/?api=1&destination=" + quote(f"{theatre}, {city}", safe="")


def send_to_user(db: Session, user_id: int, payload: dict) -> int:
    """Send to every device of a user. Dead devices are forgotten. Returns how many accepted it."""
    ok = 0
    for sub in db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all():
        try:
            _sender(sub, payload)
            ok += 1
        except SubscriptionGone:
            db.delete(sub)
        except Exception as exc:  # a flaky push server must not break the others
            log.warning("push to subscription %s failed: %s", sub.id, exc)
    db.commit()
    return ok


def send_due_reminders(db: Session, now: datetime | None = None) -> int:
    """Notify owners of paid bookings whose show starts within reminder_minutes.
    Once per booking (reminders_sent.booking_id is UNIQUE). Returns bookings notified."""
    now = now or datetime.now(timezone.utc)
    soon = now + timedelta(minutes=settings.reminder_minutes)
    due = db.scalars(
        select(Booking).join(Showtime, Showtime.id == Booking.showtime_id)
        .where(Booking.status == "confirmed", Showtime.starts_at > now, Showtime.starts_at <= soon,
               ~select(ReminderSent.id).where(ReminderSent.booking_id == Booking.id).exists(),
               select(PushSubscription.id).where(PushSubscription.user_id == Booking.user_id).exists())
    ).all()
    sent = 0
    for b in due:
        try:                          # CLAIM the reminder: the UNIQUE booking_id lets only one worker win
            claim = ReminderSent(booking_id=b.id)
            db.add(claim)
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        minutes = max(1, int((b.showtime.starts_at - now).total_seconds() // 60) + 1)
        payload = {
            "title": f"{b.movie_title} starts in {minutes} min",
            "body": f"Have you reached {b.theatre_name}? Tap for directions. Seats {', '.join(b.seat_labels)}.",
            "tag": f"booking-{b.id}",
            "url": directions_url(b.theatre_name, b.city),
            "bookingId": b.id,
        }
        if send_to_user(db, b.user_id, payload) > 0:
            sent += 1
        else:                         # nobody accepted it: release the claim so the next run retries
            db.delete(db.get(ReminderSent, claim.id))
            db.commit()
    return sent
