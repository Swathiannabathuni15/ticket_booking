import contextlib

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Booking, ShowtimeSeat, User
from app.rate_limit_deps import limit_bookings_per_user
from app.redis_client import get_redis
from app.schemas import BookingCreate, BookingOut
from app.services import booking_service as svc
from app.services import hold_service as holds

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post(
    "",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_bookings_per_user)],
)
def create_booking(
    body: BookingCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    r: redis.Redis = Depends(get_redis),
):
    seat_ids = body.seat_ids
    found = set(db.scalars(select(ShowtimeSeat.id).where(ShowtimeSeat.id.in_(seat_ids))).all())
    missing = [sid for sid in seat_ids if sid not in found]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Seat(s) not found: {missing}")

    # An idempotent retry already proved it held the seats; only a NEW booking
    # needs a live hold (the first call used it up - see the `else` branch below).
    retrying = svc.find_by_key(db, user.id, idempotency_key) is not None
    if not retrying:
        try:
            if not holds.has_valid_holds(r, seat_ids, user.id):
                raise HTTPException(status.HTTP_409_CONFLICT, "Hold every seat first: POST /holds")
        except holds.HoldUnavailable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Seat holds are temporarily unavailable, try again shortly",
            ) from None

    try:
        booking, created = svc.book_seats(db, user.id, seat_ids, idempotency_key)
    except svc.SeatNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Seat not found: {exc.args[0]}") from exc
    except svc.SeatsSpanMultipleShowtimes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "All seats in one booking must belong to the same showtime"
        ) from None
    except svc.SeatAlreadyBooked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Seat(s) already booked: {exc.args[0]}") from exc
    if not created:
        response.status_code = status.HTTP_200_OK  # a retry, not a new booking
    else:
        with contextlib.suppress(holds.HoldUnavailable):  # booking is committed; holds just expire on their own
            holds.release_holds(r, seat_ids, user.id)
    return booking


@router.get("/me", response_model=list[BookingOut])
def my_bookings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Booking).where(Booking.user_id == user.id).order_by(Booking.id.desc())
    return db.scalars(stmt).all()


@router.delete("/{booking_id}", response_model=BookingOut)
def cancel(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return svc.cancel_booking(db, user.id, booking_id)
    except svc.BookingNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found") from None
    except svc.NotYourBooking:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your booking") from None
