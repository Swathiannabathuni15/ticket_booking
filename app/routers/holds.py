"""Seat holds: temporary reservations backed by Redis (see hold_service).

Holding several seats at once (one order) is all-or-nothing: see
app/services/hold_service.py's module docstring for why that needs a single
Lua script rather than a loop of single-seat acquires.
"""
from fastapi import APIRouter, Depends, HTTPException, status
import redis
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

    # Cumulative cap per showtime: the per-request limit (max 8 seat_ids,
    # enforced by HoldCreate) only bounds one call - without this, someone
    # could call /holds repeatedly to pile up far more holds on the same
    # showtime over time. Grouped by showtime because a single request can
    # technically mix seats from different showtimes (booking rejects that
    # later; holding doesn't need to).
    requested_by_showtime: dict[int, list[int]] = {}
    for sid in seat_ids:
        requested_by_showtime.setdefault(seat_showtime[sid], []).append(sid)
    try:
        for showtime_id, requested in requested_by_showtime.items():
            all_seat_ids_for_showtime = db.scalars(
                select(ShowtimeSeat.id).where(ShowtimeSeat.showtime_id == showtime_id)
            ).all()
            currently_held = holds.seats_held_by_user(r, all_seat_ids_for_showtime, user.id)
            net_after = (currently_held - set(requested)) | set(requested)
            if len(net_after) > settings.max_held_seats_per_showtime:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Cannot hold more than {settings.max_held_seats_per_showtime} seats "
                    "at once on one showtime",
                )
    except holds.HoldUnavailable:
        _unavailable()

    try:
        result = holds.acquire_holds(r, seat_ids, user.id)
    except holds.SeatHeldByOther as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Seat {exc.args[0]} is held by another user")
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
