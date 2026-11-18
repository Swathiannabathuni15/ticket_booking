"""Jobs a worker executes. Each one opens its own DB session - it may run in
a different process than the one that enqueued it.
"""

from app.db import SessionLocal
from app.services.payment_service import expire_unpaid_bookings, reconcile_pending_attempts


def expire_unpaid_bookings_job() -> int:
    with SessionLocal() as db:
        reconcile_pending_attempts(db)  # first: finish payments whose webhook never came
        return expire_unpaid_bookings(db)


def send_due_reminders_job() -> int:
    from app.services.push_service import send_due_reminders

    with SessionLocal() as db:
        return send_due_reminders(db)


def extend_schedule_job() -> int:
    from app.redis_client import get_redis
    from app.services.schedule_service import extend_schedule_if_due

    with SessionLocal() as db:
        return extend_schedule_if_due(db, get_redis())


def send_pending_confirmations_job() -> int:
    from app.services.ticket_email import send_pending_confirmations

    with SessionLocal() as db:
        return send_pending_confirmations(db)
