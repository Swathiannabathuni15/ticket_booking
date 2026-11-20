"""Booking logic. This is the heart of the project.

THE PROBLEM
    Two users click "book" on the last seat at the same moment.
    Naive code does: (1) check if seat is free  (2) insert booking.
    Both requests pass step 1 before either reaches step 2 -> double booking.

THE FIX (two layers)
    Layer 1 - pessimistic row lock:
        `SELECT ... FOR UPDATE` on the ShowtimeSeat row(s). Any other
        transaction wanting the same seat must WAIT until this one commits
        or rolls back, and then sees the seat is taken.
    Layer 2 - database constraint:
        A partial unique index on booking_seats(showtime_seat_id) WHERE
        status IN ('pending_payment', 'confirmed'). Even if Layer 1 had a
        bug, Postgres refuses the second row.

IDEMPOTENCY
    A client can send an `Idempotency-Key`. If the same user retries with the
    same key (double click, network retry), they get the original booking back
    instead of a new one or an error.

TWO-STEP BOOKING
    book_seats() only creates the booking as 'pending_payment' - it does not
    confirm it. Confirming happens in app/services/payment_service.py. A
    seat counts as taken (for both the lock-bypass index above and for
    availability checks elsewhere) as soon as it is pending_payment, so
    nobody else can grab it while the first booker is paying.

MULTI-SEAT BOOKING AND WHY THE LOCK ORDER MATTERS
    One booking can cover several seats (e.g. a family buying 4 tickets).
    All of them are locked in a single query, ordered by id:
    `WHERE id IN (...) ORDER BY id FOR UPDATE`. The ordering is what avoids a
    classic deadlock: if user A books seats [3, 7] and user B books seats
    [7, 3] at the same moment, and each locked them in the order given in
    the request, A could end up waiting on the lock B holds while B waits on
    the lock A holds - neither can proceed. Locking in a fixed, id-sorted
    order means every transaction that wants seat 3 and seat 7 always
    acquires seat 3 first, so the two transactions simply queue up behind
    each other instead of deadlocking.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, BookingSeat, ShowtimeSeat

ACTIVE_STATUSES = ("pending_payment", "confirmed")


class SeatNotFound(Exception):
    pass


class SeatAlreadyBooked(Exception):
    pass


class SeatsSpanMultipleShowtimes(Exception):
    """All seats in one booking must belong to the same showtime - an order
    is "these seats, for this showing", not a cart across different ones."""


class BookingNotFound(Exception):
    pass


class NotYourBooking(Exception):
    pass


def find_by_key(db: Session, user_id: int, key: str | None) -> Booking | None:
    """Exposed so the router can skip the hold check on idempotent retries."""
    if not key:
        return None
    return db.scalar(select(Booking).where(Booking.user_id == user_id, Booking.idempotency_key == key))


def book_seats(
    db: Session, user_id: int, seat_ids: list[int], idempotency_key: str | None = None
) -> tuple[Booking, bool]:
    """Book one or more ShowtimeSeats as a single order. Returns (booking, created).

    `created` is False when an earlier booking with the same idempotency key
    was returned instead of making a new one.
    """
    existing = find_by_key(db, user_id, idempotency_key)
    if existing:
        return existing, False

    try:
        # Lock every seat row in one query, in a FIXED order (by id) - see
        # module docstring for why that ordering is what avoids deadlocks
        # between two bookings that share some of the same seats.
        seats = db.scalars(
            select(ShowtimeSeat)
            .where(ShowtimeSeat.id.in_(seat_ids))
            .order_by(ShowtimeSeat.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        found_ids = {seat.id for seat in seats}
        missing = [sid for sid in seat_ids if sid not in found_ids]
        if missing:
            raise SeatNotFound(missing[0])

        showtime_ids = {seat.showtime_id for seat in seats}
        if len(showtime_ids) > 1:
            raise SeatsSpanMultipleShowtimes(seat_ids)

        already_taken = db.scalars(
            select(BookingSeat.showtime_seat_id).where(
                BookingSeat.showtime_seat_id.in_(seat_ids), BookingSeat.status.in_(ACTIVE_STATUSES)
            )
        ).all()
        if already_taken:
            raise SeatAlreadyBooked(already_taken[0])

        now = datetime.now(UTC)
        booking = Booking(
            user_id=user_id,
            showtime_id=seats[0].showtime_id,
            idempotency_key=idempotency_key,
            status="pending_payment",
            payment_due_at=now + timedelta(minutes=settings.payment_due_minutes),
        )
        db.add(booking)
        db.flush()  # gives booking.id without committing yet
        db.add_all(
            BookingSeat(booking_id=booking.id, showtime_seat_id=seat.id, status="pending_payment") for seat in seats
        )
        db.commit()  # commit releases the row locks
        return booking, True

    except (SeatAlreadyBooked, IntegrityError) as exc:
        db.rollback()
        # Maybe this was a retry racing with itself: same key, so return the original.
        existing = find_by_key(db, user_id, idempotency_key)
        if existing:
            return existing, False
        if isinstance(exc, IntegrityError):
            raise SeatAlreadyBooked(seat_ids) from exc
        raise
    except Exception:
        db.rollback()  # always release the locks on any error
        raise


def cancel_booking(db: Session, user_id: int, booking_id: int) -> Booking:
    try:
        booking = db.scalar(
            select(Booking).where(Booking.id == booking_id).with_for_update().execution_options(populate_existing=True)
        )
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.user_id != user_id:
            raise NotYourBooking(booking_id)
        if booking.status != "cancelled":
            booking.status = "cancelled"
            booking.cancelled_at = datetime.now(UTC)
            # BookingSeat.status is a denormalized copy - see app/models.py - so
            # it has to be updated in lockstep, in the same transaction.
            db.execute(update(BookingSeat).where(BookingSeat.booking_id == booking_id).values(status="cancelled"))
            # A cancelled booking can't be paid for by friends either (A5).
            from app.services import split_service

            split_service.cancel_split_for_booking_in_txn(db, booking_id)
            db.commit()
        return booking
    except Exception:
        db.rollback()
        raise
