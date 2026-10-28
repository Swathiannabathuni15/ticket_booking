"""Fake payments: no real provider, just the state machine and the two
protections a real payment step needs.

TWO DIFFERENT "DON'T CHARGE TWICE" PROBLEMS
    1. A network retry: the client sent the exact same payment request twice
       (double click, dropped response, client-side retry). The
       `Idempotency-Key` handles this - same user + same key always returns
       the same Payment row, never creates a second one.
    2. A genuine second payment attempt: someone tries to pay for a booking
       that is already confirmed, with a DIFFERENT idempotency key (e.g. they
       don't realise the first payment went through). This is rejected with
       AlreadyPaid - a booking can be paid for exactly once, full stop.

WHY A SEPARATE Payment TABLE, UNIQUE ON booking_id
    Booking.idempotency_key is already used up by the booking-creation step
    (see booking_service.book_seat). Payment gets its own idempotency key,
    and its own database-level backstop: `booking_id` is UNIQUE, so even a
    bug that let two concurrent payment attempts both pass the status check
    would have the second INSERT rejected by Postgres - the same "app logic
    plus a DB constraint behind it" pattern as the booking lock.
"""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking, Payment


class BookingNotFound(Exception):
    pass


class NotYourBooking(Exception):
    pass


class AlreadyPaid(Exception):
    pass


class BookingNotPayable(Exception):
    """Cancelled or expired - there is nothing left to pay for."""


def _find_payment_by_key(db: Session, user_id: int, key: str) -> Payment | None:
    return db.scalar(
        select(Payment).where(Payment.user_id == user_id, Payment.idempotency_key == key)
    )


def pay_booking(db: Session, user_id: int, booking_id: int, idempotency_key: str) -> tuple[Booking, bool]:
    """Confirm payment for a booking. Returns (booking, created).

    `created` is False when an earlier payment with the same idempotency key
    was found and returned instead of a new one being made.
    """
    existing = _find_payment_by_key(db, user_id, idempotency_key)
    if existing:
        return db.get(Booking, existing.booking_id), False

    try:
        # Lock the booking row, same reasoning as the seat lock in book_seat:
        # a concurrent payment attempt on the same booking must wait here.
        booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.user_id != user_id:
            raise NotYourBooking(booking_id)
        if booking.status == "confirmed":
            raise AlreadyPaid(booking_id)
        if booking.status != "pending_payment":
            raise BookingNotPayable(booking_id)

        payment = Payment(booking_id=booking.id, user_id=user_id, idempotency_key=idempotency_key)
        db.add(payment)
        booking.status = "confirmed"
        booking.paid_at = datetime.now(timezone.utc)
        db.commit()
        return booking, True

    except (AlreadyPaid, IntegrityError) as exc:
        db.rollback()
        # Maybe this was a retry racing with itself: same key, so return the original.
        existing = _find_payment_by_key(db, user_id, idempotency_key)
        if existing:
            return db.get(Booking, existing.booking_id), False
        if isinstance(exc, IntegrityError):
            raise AlreadyPaid(booking_id) from exc
        raise
    except Exception:
        db.rollback()
        raise


def expire_unpaid_bookings(db: Session) -> int:
    """Cancel every pending_payment booking whose payment_due_at has passed,
    freeing the seat (the partial unique index only blocks pending_payment
    and confirmed, so cancelling clears it). Returns the number cancelled.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Booking)
        .where(Booking.status == "pending_payment", Booking.payment_due_at < now)
        .values(status="cancelled", cancelled_at=now)
    )
    db.commit()
    return result.rowcount
