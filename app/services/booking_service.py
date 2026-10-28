"""Booking logic. This is the heart of the project.

THE PROBLEM
    Two users click "book" on the last seat at the same moment.
    Naive code does: (1) check if seat is free  (2) insert booking.
    Both requests pass step 1 before either reaches step 2 -> double booking.

THE FIX (two layers)
    Layer 1 - pessimistic row lock:
        `SELECT ... FOR UPDATE` on the seat row. The second transaction must
        WAIT until the first commits, and then sees the seat is taken.
    Layer 2 - database constraint:
        A partial unique index on bookings(seat_id) WHERE status IN
        ('pending_payment', 'confirmed'). Even if Layer 1 had a bug, Postgres
        refuses the second row.

IDEMPOTENCY
    A client can send an `Idempotency-Key`. If the same user retries with the
    same key (double click, network retry), they get the original booking back
    instead of a new one or an error.

TWO-STEP BOOKING
    book_seat() only creates the booking as 'pending_payment' - it does not
    confirm it. Confirming happens in app/services/payment_service.py. A
    seat counts as taken (for both the lock-bypass index above and for
    availability checks elsewhere) as soon as it is pending_payment, so
    nobody else can grab it while the first booker is paying.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, Seat

ACTIVE_STATUSES = ("pending_payment", "confirmed")


class SeatNotFound(Exception):
    pass


class SeatAlreadyBooked(Exception):
    pass


class BookingNotFound(Exception):
    pass


class NotYourBooking(Exception):
    pass


def find_by_key(db: Session, user_id: int, key: str | None) -> Booking | None:
    """Exposed so the router can skip the hold check on idempotent retries."""
    if not key:
        return None
    return db.scalar(
        select(Booking).where(Booking.user_id == user_id, Booking.idempotency_key == key)
    )


def book_seat(
    db: Session, user_id: int, seat_id: int, idempotency_key: str | None = None
) -> tuple[Booking, bool]:
    """Book a seat. Returns (booking, created).

    `created` is False when an earlier booking with the same idempotency key
    was returned instead of making a new one.
    """
    existing = find_by_key(db, user_id, idempotency_key)
    if existing:
        return existing, False

    try:
        # Lock the seat row. Anyone else trying to book this seat waits here
        # until we commit or roll back.
        seat = db.scalar(select(Seat).where(Seat.id == seat_id).with_for_update())
        if seat is None:
            raise SeatNotFound(seat_id)

        already_taken = db.scalar(
            select(Booking.id).where(Booking.seat_id == seat_id, Booking.status.in_(ACTIVE_STATUSES))
        )
        if already_taken:
            raise SeatAlreadyBooked(seat_id)

        now = datetime.now(timezone.utc)
        booking = Booking(
            user_id=user_id,
            seat_id=seat_id,
            idempotency_key=idempotency_key,
            status="pending_payment",
            payment_due_at=now + timedelta(minutes=settings.payment_due_minutes),
        )
        db.add(booking)
        db.commit()  # commit releases the row lock
        return booking, True

    except (SeatAlreadyBooked, IntegrityError) as exc:
        db.rollback()
        # Maybe this was a retry racing with itself: same key, so return the original.
        existing = find_by_key(db, user_id, idempotency_key)
        if existing:
            return existing, False
        if isinstance(exc, IntegrityError):
            raise SeatAlreadyBooked(seat_id) from exc
        raise
    except Exception:
        db.rollback()  # always release the lock on any error
        raise


def cancel_booking(db: Session, user_id: int, booking_id: int) -> Booking:
    try:
        booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.user_id != user_id:
            raise NotYourBooking(booking_id)
        if booking.status != "cancelled":
            booking.status = "cancelled"
            booking.cancelled_at = datetime.now(timezone.utc)
            db.commit()
        return booking
    except Exception:
        db.rollback()
        raise
