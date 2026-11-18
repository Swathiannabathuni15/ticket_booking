"""E-tickets: the owner's ticket (with QR code), a calendar file, "email it to me", the public ticket page that
a QR scan opens, and check-in at the door for staff. See app/services/ticket_service.py."""

import redis
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db, required
from app.deps import get_current_user, require_admin
from app.models import Booking, User
from app.redis_client import get_redis
from app.schemas import CheckInOut, TicketOut, TicketPublicOut
from app.services import rate_limit, ticket_email, ticket_service
from app.services.otp_delivery import DeliveryFailed, email_configured

router = APIRouter(tags=["tickets"])

EMAIL_RESENDS_PER_HOUR = 3


def _my_paid_booking(db: Session, booking_id: int, user: User) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    if booking.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your booking")
    if booking.status != "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no ticket: this booking is not paid")
    if not booking.ticket_code:  # paid before e-tickets existed: give it one now
        locked = required(db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update()), "booking")
        ticket_service.issue_ticket(db, locked)
        db.commit()
        db.refresh(booking)
    return booking


@router.get("/bookings/{booking_id}/ticket", response_model=TicketOut)
def my_ticket(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return ticket_service.owner_view(_my_paid_booking(db, booking_id, user))


@router.get("/bookings/{booking_id}/ticket.ics")
def my_ticket_calendar(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = _my_paid_booking(db, booking_id, user)
    return Response(
        ticket_service.ics_for(booking),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ticket-{booking.id}.ics"'},
    )


@router.post("/bookings/{booking_id}/ticket/email")
def email_my_ticket(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    r: redis.Redis = Depends(get_redis),
):
    booking = _my_paid_booking(db, booking_id, user)
    if not user.email:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Your account has no email address (you signed in with a phone number)"
        )
    if not email_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Email is not set up on this server")
    try:
        rate_limit.check(r, f"ticketmail:{user.id}", EMAIL_RESENDS_PER_HOUR, 3600)
    except rate_limit.RateLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You have asked for this email a few times already. Try again later.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    try:
        ticket_email.send_confirmation(db, booking.id, force=True)
    except DeliveryFailed:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The email could not be sent. Please try again.") from None
    return {"sent_to": user.email}


@router.get("/tickets/{code}", response_model=TicketPublicOut)
def ticket_by_code(code: str, db: Session = Depends(get_db)):
    """What a QR scan opens. Anyone with the code sees the show, seats and whether it is still valid -
    nothing about who bought it."""
    norm = ticket_service.normalize(code)
    booking = db.scalar(select(Booking).where(Booking.ticket_code == norm)) if norm else None
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket_service.public_view(booking)


@router.post("/tickets/{code}/check-in", response_model=CheckInOut)
def check_in(code: str, db: Session = Depends(get_db), _staff: User = Depends(require_admin)):
    return ticket_service.check_in(db, code)
