"""Jobs a worker executes. Each one opens its own DB session - it may run in
a different process than the one that enqueued it.
"""
from app.db import SessionLocal
from app.services.payment_service import expire_unpaid_bookings


def expire_unpaid_bookings_job() -> int:
    with SessionLocal() as db:
        return expire_unpaid_bookings(db)
