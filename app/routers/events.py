from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import Booking, Event, Seat, User
from app.schemas import EventCreate, EventOut, SeatOut
from app.services.booking_service import ACTIVE_STATUSES

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    body: EventCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    event = Event(name=body.name, venue=body.venue, starts_at=body.starts_at)
    db.add(event)
    db.flush()  # gives event.id without committing yet
    row_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[: body.rows]
    db.add_all(
        Seat(event_id=event.id, label=f"{letter}{n}", price=body.price)
        for letter in row_letters
        for n in range(1, body.seats_per_row + 1)
    )
    db.commit()
    return event


@router.get("", response_model=list[EventOut])
def list_events(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))  # never let a client ask for unlimited rows
    stmt = select(Event).order_by(Event.starts_at).limit(limit).offset(max(offset, 0))
    return db.scalars(stmt).all()


@router.get("/{event_id}/seats", response_model=list[SeatOut])
def list_seats(event_id: int, db: Session = Depends(get_db)):
    if db.get(Event, event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    # LEFT JOIN: a seat is available when it has no active (pending or confirmed) booking.
    stmt = (
        select(Seat, Booking.id)
        .outerjoin(Booking, and_(Booking.seat_id == Seat.id, Booking.status.in_(ACTIVE_STATUSES)))
        .where(Seat.event_id == event_id)
        .order_by(Seat.id)
    )
    return [
        SeatOut(id=s.id, label=s.label, price=float(s.price), available=booking_id is None)
        for s, booking_id in db.execute(stmt).all()
    ]
