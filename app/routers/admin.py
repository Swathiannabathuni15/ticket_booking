"""Admin-only read endpoints for the small admin UI (B5)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import Booking, User
from app.schemas import AdminBookingOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/bookings", response_model=list[AdminBookingOut])
def all_bookings(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    limit = max(1, min(limit, 200))
    stmt = select(Booking).order_by(Booking.id.desc()).limit(limit).offset(max(offset, 0))
    if status:
        stmt = stmt.where(Booking.status == status)
    return db.scalars(stmt).all()
