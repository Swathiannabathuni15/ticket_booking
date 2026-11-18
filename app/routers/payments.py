"""Pay for a booking (see app/services/payment_service.py for the design) and
receive the bank's signed webhooks."""

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit_deps import limit_payments_per_user
from app.schemas import BookingOut, PaymentCreate
from app.services import fake_gateway
from app.services import payment_service as svc

router = APIRouter(tags=["payments"])


@router.post(
    "/payments",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_payments_per_user)],
    responses={
        202: {"description": "Processing: the bank has not answered yet; poll /bookings/me"},
        402: {"description": "Card declined"},
    },
)
def pay(
    body: PaymentCreate,
    response: Response,
    idempotency_key: str = Header(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    method = body.payment_method if settings.gateway_allow_test_methods else "tok_ok"
    try:
        booking, outcome = svc.pay_booking(db, user.id, body.booking_id, idempotency_key, method)
    except svc.BookingNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found") from None
    except svc.NotYourBooking:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your booking") from None
    except svc.AlreadyPaid:
        raise HTTPException(status.HTTP_409_CONFLICT, "This booking has already been paid") from None
    except svc.BookingNotPayable:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This booking is cancelled or expired and can't be paid"
        ) from None
    except svc.PaymentInProgress:
        raise HTTPException(status.HTTP_409_CONFLICT, "A payment for this booking is already being processed") from None
    except svc.KeyReused:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This idempotency key was already used for a different booking"
        ) from None
    except svc.PaymentDeclined as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc) or "Payment failed") from exc
    if outcome == "replayed":
        response.status_code = status.HTTP_200_OK
    elif outcome == "processing":
        response.status_code = status.HTTP_202_ACCEPTED
    return booking


@router.post("/webhooks/payments")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """Called by the BANK, not by a browser. No login: the signature is the
    credential. We answer 2xx only once the event is safely processed, so the
    bank keeps retrying if we hit an error."""
    raw = await request.body()
    if not fake_gateway.verify(raw, request.headers.get("X-Gateway-Signature")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature")
    try:
        event = json.loads(raw)
        event["id"], event["type"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed event") from None
    result = svc.handle_webhook(db, event)
    return {"received": True, "result": result}
