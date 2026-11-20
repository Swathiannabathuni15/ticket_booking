"""Seat holds: temporary reservations backed by Redis (see hold_service).

Holding several seats at once (one order) is all-or-nothing: see
app/services/hold_service.py's module docstring for why that needs a single
Lua script rather than a loop of single-seat acquires.
"""

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import BookingSeat, ShowtimeSeat, User
from app.rate_limit_deps import limit_holds_per_user
from app.redis_client import get_redis
from app.schemas import HoldCreate, HoldOut
from app.services import hold_service as holds
from app.services.booking_service import ACTIVE_STATUSES

router = APIRouter(prefix="/holds", tags=["holds"])


def _unavailable():
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Seat holds are temporarily unavailable, try again shortly"
    )


@router.post(
    "", response_model=HoldOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_holds_per_user)]
)
def create_hold(
    body: HoldCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    r: redis.Redis = Depends(get_redis),
):
    seat_ids = body.seat_ids
    seat_showtime = dict(
        db.execute(select(ShowtimeSeat.id, ShowtimeSeat.showtime_id).where(ShowtimeSeat.id.in_(seat_ids))).all()
    )
    missing = [sid for sid in seat_ids if sid not in seat_showtime]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Seat(s) not found: {missing}")

    already_booked = set(
        db.scalars(
            select(BookingSeat.showtime_seat_id).where(
                BookingSeat.showtime_seat_id.in_(seat_ids), BookingSeat.status.in_(ACTIVE_STATUSES)
            )
        ).all()
    )
    if already_booked:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Seat(s) already booked: {sorted(already_booked)}")

    # Also capped in TOTAL per showtime (MAX_HELD_SEATS_PER_SHOWTIME), across however many calls it
    # takes - checked inside the same atomic Redis step that takes the seats, so two requests sent at
    # once cannot both slip under the limit.
    try:
        result = holds.acquire_holds(r, seat_ids, user.id, seat_showtime)
    except holds.SeatHeldByOther as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Seat {exc.args[0]} is held by another user") from exc
    except holds.HoldCapExceeded:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot hold more than {settings.max_held_seats_per_showtime} seats at once on one showtime",
        ) from None
    except holds.HoldUnavailable:
        _unavailable()
    return HoldOut(seat_ids=result["seat_ids"], ttl_seconds=result["ttl_seconds"])


@router.delete("/{seat_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(limit_holds_per_user)])
def release_hold(
    seat_id: int,
    user: User = Depends(get_current_user),
    r: redis.Redis = Depends(get_redis),
):
    try:
        holds.release_hold(r, seat_id, user.id)
    except holds.HoldUnavailable:
        _unavailable()
