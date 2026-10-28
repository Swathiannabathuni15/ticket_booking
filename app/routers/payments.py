"""Fake payment confirmation (see app/services/payment_service.py)."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import BookingOut, PaymentCreate
from app.services import payment_service as svc

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def pay(
    body: PaymentCreate,
    response: Response,
    idempotency_key: str = Header(..., max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        booking, created = svc.pay_booking(db, user.id, body.booking_id, idempotency_key)
    except svc.BookingNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    except svc.NotYourBooking:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your booking")
    except svc.AlreadyPaid:
        raise HTTPException(status.HTTP_409_CONFLICT, "This booking has already been paid")
    except svc.BookingNotPayable:
        raise HTTPException(status.HTTP_409_CONFLICT, "This booking is cancelled or expired and can't be paid")
    if not created:
        response.status_code = status.HTTP_200_OK  # a retry, not a new payment
    return booking
