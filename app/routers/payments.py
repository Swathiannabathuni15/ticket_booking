"""Pay for a booking (see app/services/payment_service.py for the design) and
receive the bank's signed webhooks."""

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Booking, User
from app.rate_limit_deps import limit_payments_per_user
from app.schemas import BookingOut, PaymentCreate, RazorpayOrderIn, RazorpayOrderOut, RazorpayVerifyIn
from app.services import fake_gateway, razorpay_gateway
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
    except svc.WrongProvider:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This server takes payments through Razorpay. Use the Razorpay checkout."
        ) from None
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


# ---------------------------------------------------------------- Razorpay


def _need_razorpay() -> None:
    if settings.payment_provider != "razorpay":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Razorpay is not the payment provider on this server")
    if not razorpay_gateway.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Payments are not set up on this server yet")


def _razorpay_errors(exc: Exception) -> HTTPException:
    """Turn the service's exceptions into answers (the same ones the card endpoint gives)."""
    table: list[tuple[type, int, str]] = [
        (svc.BookingNotFound, status.HTTP_404_NOT_FOUND, "Booking not found"),
        (svc.NotYourBooking, status.HTTP_403_FORBIDDEN, "Not your booking"),
        (svc.AlreadyPaid, status.HTTP_409_CONFLICT, "This booking has already been paid"),
        (svc.BookingNotPayable, status.HTTP_409_CONFLICT, "This booking is cancelled or expired and can't be paid"),
        (svc.PaymentInProgress, status.HTTP_409_CONFLICT, "A payment for this booking is already being processed"),
        (svc.KeyReused, status.HTTP_409_CONFLICT, "This idempotency key was already used for a different booking"),
        (svc.PaymentDeclined, status.HTTP_402_PAYMENT_REQUIRED, str(exc) or "Payment failed"),
        (svc.BadSignature, status.HTTP_400_BAD_REQUEST, "The payment could not be verified"),
        (
            razorpay_gateway.GatewayUnavailable,
            status.HTTP_502_BAD_GATEWAY,
            "The payment service did not answer. Please try again.",
        ),
    ]
    for kind, code, message in table:
        if isinstance(exc, kind):
            return HTTPException(code, message)
    raise exc


@router.post(
    "/payments/razorpay/order",
    response_model=RazorpayOrderOut,
    dependencies=[Depends(limit_payments_per_user)],
)
def razorpay_order(
    body: RazorpayOrderIn,
    idempotency_key: str = Header(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Step 1: get (or re-get) the Razorpay order for a booking. The order id is saved in our database
    first; asking again, even with a different key, returns the same order."""
    _need_razorpay()
    try:
        attempt, order = svc.start_razorpay_order(db, user.id, body.booking_id, idempotency_key)
    except (svc.PaymentError, razorpay_gateway.GatewayUnavailable) as exc:
        raise _razorpay_errors(exc) from None
    booking = db.get(Booking, attempt.booking_id)
    return RazorpayOrderOut(
        key_id=settings.razorpay_key_id,
        order_id=order["id"],
        amount=order["amount"],
        currency=order["currency"],
        booking_id=attempt.booking_id,
        name=settings.razorpay_checkout_name,
        description=f"{booking.movie_title}, seats {', '.join(booking.seat_labels)}" if booking else "Movie tickets",
    )


@router.post("/payments/razorpay/verify", response_model=BookingOut, dependencies=[Depends(limit_payments_per_user)])
def razorpay_verify(body: RazorpayVerifyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Step 3a: the browser says it paid. Only a valid Razorpay signature counts."""
    _need_razorpay()
    try:
        return svc.confirm_razorpay_payment(
            db, user.id, body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
        )
    except svc.PaymentError as exc:
        raise _razorpay_errors(exc) from None


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Step 3b: called by RAZORPAY, not a browser. No login: the signature is the credential. We answer 2xx
    only once the event is safely processed, so Razorpay keeps retrying if we hit an error."""
    raw = await request.body()
    if not razorpay_gateway.verify_webhook(raw, request.headers.get("X-Razorpay-Signature")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature")
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed event") from None
    # Razorpay numbers every delivery of an event with the same id, so a retry carries the same one.
    event_id = request.headers.get("X-Razorpay-Event-Id") or "body-" + hashlib.sha256(raw).hexdigest()[:40]
    result = svc.handle_razorpay_webhook(db, event_id, event)
    return {"received": True, "result": result}
