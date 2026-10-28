"""Seat holds: temporary reservations backed by Redis (see hold_service)."""
from fastapi import APIRouter, Depends, HTTPException, status
import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Booking, Seat, User
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
    if db.get(Seat, body.seat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Seat not found")
    already_booked = db.scalar(
        select(Booking.id).where(Booking.seat_id == body.seat_id, Booking.status.in_(ACTIVE_STATUSES))
    )
    if already_booked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat already booked")
    try:
        result = holds.acquire_hold(r, body.seat_id, user.id)
    except holds.SeatHeldByOther:
        raise HTTPException(status.HTTP_409_CONFLICT, "Seat is held by another user")
    except holds.HoldUnavailable:
        _unavailable()
    return HoldOut(seat_id=result["seat_id"], ttl_seconds=result["ttl_seconds"])


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
