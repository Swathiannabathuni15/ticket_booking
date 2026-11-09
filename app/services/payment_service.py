"""Payments, built the way real card payments are built.

THE PROBLEM
    Paying involves a call to a bank over a network. Networks fail: the
    request can be lost, or the bank can charge the card and our reply can be
    lost (phone dies, wifi drops, server restarts). We must never charge twice
    and never lose a payment that really happened.

THE SOLUTION (each piece answers one failure)
    1. CLIENT IDEMPOTENCY KEY. The client makes a UUID per payment attempt and
       sends it as `Idempotency-Key`. The same key always means the same attempt.
    2. WRITE THE ATTEMPT FIRST. A `payment_attempts` row (status `pending`) is
       saved BEFORE the bank is called. If we crash right after, we still know
       exactly what we asked the bank to do.
    3. THE BANK IS IDEMPOTENT TOO. We pass it "<user>:<uuid>" as its own
       idempotency key, so the bank charges once per key however often we ask
       (see fake_gateway.charge). A retry is therefore always safe.
    4. THE REPLY IS NOT THE ONLY WAY TO LEARN THE RESULT.
         a. WEBHOOK: the bank calls POST /webhooks/payments, signed, whether or
            not our reply got through (see handle_webhook).
         b. RETRY: the client re-sends the same key; we ask the bank "what
            happened to this key?" (pay_booking, pending branch).
         c. SWEEPER: a background job asks the bank about attempts that are
            still pending after a while (reconcile_pending_attempts).
       All three end in `_settle_success`, which is idempotent, so it does not
       matter which one wins or if all three run.
    5. PAID AFTER EXPIRY. If the money arrives after the booking was cancelled
       (seats may already be sold again) or was already paid by another
       attempt, we refund instead of confirming.
    6. DB BACKSTOPS: payments.booking_id UNIQUE, payment_attempts
       (user_id, idempotency_key) UNIQUE, webhook_events.event_id UNIQUE.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, BookingSeat, Payment, PaymentAttempt, WebhookEvent
from app.services import fake_gateway
from app.services.split_service import booking_total_cents

log = logging.getLogger(__name__)


class BookingNotFound(Exception):
    pass


class NotYourBooking(Exception):
    pass


class AlreadyPaid(Exception):
    pass


class BookingNotPayable(Exception):
    """Cancelled or expired - there is nothing left to pay for."""


class PaymentDeclined(Exception):
    pass


class PaymentInProgress(Exception):
    """Another attempt (different key) for this booking is still being processed."""


class KeyReused(Exception):
    """The same idempotency key was used for a different booking."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find_attempt(db: Session, user_id: int, key: str) -> PaymentAttempt | None:
    return db.scalar(
        select(PaymentAttempt).where(PaymentAttempt.user_id == user_id, PaymentAttempt.idempotency_key == key)
    )


def _start_attempt(db: Session, user_id: int, booking_id: int, key: str) -> tuple[PaymentAttempt, bool]:
    """Validate and record a new pending attempt. Returns (attempt, is_new)."""
    try:
        booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.user_id != user_id:
            raise NotYourBooking(booking_id)
        if booking.status == "confirmed":
            raise AlreadyPaid(booking_id)
        if booking.status != "pending_payment":
            raise BookingNotPayable(booking_id)
        same_key = _find_attempt(db, user_id, key)   # a concurrent request with this key got here first
        if same_key is not None:
            db.commit()
            return same_key, False
        in_flight = db.scalar(select(PaymentAttempt.id).where(
            PaymentAttempt.booking_id == booking_id, PaymentAttempt.status == "pending"))
        if in_flight is not None:
            raise PaymentInProgress(booking_id)
        attempt = PaymentAttempt(
            booking_id=booking_id, user_id=user_id, idempotency_key=key, gateway_key=f"{user_id}:{key}",
            amount_cents=booking_total_cents(db, booking_id), status="pending",
        )
        db.add(attempt)
        db.commit()  # saved BEFORE the bank is called
        return attempt, True
    except IntegrityError:  # the same key raced with itself: use the winner's row
        db.rollback()
        existing = _find_attempt(db, user_id, key)
        if existing is None:
            raise
        return existing, False
    except Exception:
        db.rollback()
        raise


def _mark_failed(db: Session, attempt_id: int, reason: str) -> None:
    attempt = db.scalar(select(PaymentAttempt).where(PaymentAttempt.id == attempt_id).with_for_update())
    if attempt is not None and attempt.status == "pending":
        attempt.status = "failed"
        attempt.failure_reason = reason[:200]
    db.commit()


def _settle_success(db: Session, attempt_id: int, charge_id: str) -> Booking:
    """The bank says the money moved. Confirm the booking - or refund if that
    is no longer possible. Safe to call any number of times, from anywhere."""
    refund_key = None
    try:
        booking_id = db.scalar(select(PaymentAttempt.booking_id).where(PaymentAttempt.id == attempt_id))
        # Same lock order everywhere (booking, then attempt) so two paths can't deadlock.
        booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
        attempt = db.scalar(select(PaymentAttempt).where(PaymentAttempt.id == attempt_id).with_for_update())
        if attempt.status in ("succeeded", "refunded"):
            db.commit()
            return booking
        attempt.charge_id = charge_id
        if booking.status == "pending_payment":
            db.add(Payment(booking_id=booking.id, user_id=attempt.user_id, idempotency_key=attempt.idempotency_key))
            booking.status = "confirmed"
            booking.paid_at = _now()
            # BookingSeat.status is a denormalized copy - see app/models.py.
            db.execute(update(BookingSeat).where(BookingSeat.booking_id == booking.id).values(status="confirmed"))
            attempt.status = "succeeded"
        else:
            attempt.status = "refunded"
            attempt.failure_reason = f"paid, but the booking was already {booking.status}"
            refund_key = attempt.gateway_key
        db.commit()
    except Exception:
        db.rollback()
        raise
    if refund_key:
        fake_gateway.refund(refund_key)
        log.warning("refunded charge %s: booking %s was not payable", charge_id, booking_id)
    return booking


def pay_booking(
    db: Session, user_id: int, booking_id: int, idempotency_key: str, method: str = "tok_ok"
) -> tuple[Booking, str]:
    """Pay for a booking. Returns (booking, outcome):
       "created"    - paid now
       "replayed"   - this key was already used; here is the original result
       "processing" - the bank charged (or may have) but we have no answer yet;
                      the webhook / a retry / the sweeper will finish it
    """
    attempt = _find_attempt(db, user_id, idempotency_key)
    is_new = False
    if attempt is None:
        attempt, is_new = _start_attempt(db, user_id, booking_id, idempotency_key)
    if attempt.booking_id != booking_id:
        raise KeyReused(idempotency_key)

    if not is_new:
        if attempt.status == "succeeded":
            return db.get(Booking, booking_id), "replayed"
        if attempt.status == "failed":
            raise PaymentDeclined(attempt.failure_reason or "Payment failed")
        if attempt.status == "refunded":
            raise BookingNotPayable(booking_id)
        # still pending: ask the bank what happened to this key
        charge = fake_gateway.get_charge(attempt.gateway_key)
        if charge is not None and charge.status == "succeeded":
            return _settle_success(db, attempt.id, charge.charge_id), "replayed"
        if charge is not None and charge.status == "failed":
            _mark_failed(db, attempt.id, charge.failure_reason or "Card declined")
            raise PaymentDeclined(charge.failure_reason or "Card declined")
        # the bank never saw it: sending the SAME key again is safe

    try:
        charge = fake_gateway.charge(attempt.gateway_key, attempt.amount_cents, method)
    except fake_gateway.CardDeclined as exc:
        _mark_failed(db, attempt.id, exc.reason)
        raise PaymentDeclined(exc.reason) from exc
    except fake_gateway.GatewayTimeout:
        return db.get(Booking, booking_id), "processing"
    return _settle_success(db, attempt.id, charge.charge_id), "created"


def handle_webhook(db: Session, event: dict) -> str:
    """Process one signed bank event. Returns "processed" | "duplicate" | "unknown".
    The event row and its effects commit together: if anything fails, the
    event is NOT recorded, so the bank's retry gets processed properly."""
    try:
        db.add(WebhookEvent(event_id=event["id"], type=event["type"]))
        db.flush()
    except IntegrityError:
        db.rollback()
        return "duplicate"
    try:
        data = event.get("data", {})
        attempt = db.scalar(select(PaymentAttempt).where(PaymentAttempt.gateway_key == data.get("idempotency_key")))
        if attempt is None:
            db.commit()
            return "unknown"
        if event["type"] == "payment.succeeded":
            _settle_success(db, attempt.id, data["charge_id"])  # commits the event row too
        elif event["type"] == "payment.failed":
            _mark_failed(db, attempt.id, data.get("failure_reason") or "Payment failed")
        else:
            db.commit()
        return "processed"
    except Exception:
        db.rollback()
        raise


def reconcile_pending_attempts(db: Session) -> int:
    """Safety net for a webhook that never arrives (and a crash between
    'bank charged' and 'we recorded it'). Asks the bank about old pending attempts."""
    now = _now()
    fixed = 0
    pending = db.scalars(select(PaymentAttempt).where(
        PaymentAttempt.status == "pending",
        PaymentAttempt.created_at < now - timedelta(seconds=settings.payment_reconcile_after_seconds),
    )).all()
    for a in pending:
        charge = fake_gateway.get_charge(a.gateway_key)
        if charge is not None and charge.status == "succeeded":
            _settle_success(db, a.id, charge.charge_id)
        elif charge is not None and charge.status == "failed":
            _mark_failed(db, a.id, charge.failure_reason or "Card declined")
        elif a.created_at < now - timedelta(seconds=settings.payment_give_up_after_seconds):
            _mark_failed(db, a.id, "no response from the bank")  # the bank never got it
        else:
            continue
        fixed += 1
    # we marked it refunded but crashed before telling the bank
    for a in db.scalars(select(PaymentAttempt).where(PaymentAttempt.status == "refunded")).all():
        charge = fake_gateway.get_charge(a.gateway_key)
        if charge is not None and charge.status == "succeeded":
            fake_gateway.refund(a.gateway_key)
            fixed += 1
    return fixed


def expire_unpaid_bookings(db: Session) -> int:
    """Cancel every pending_payment booking whose payment_due_at has passed,
    freeing the seat. A booking with a payment still IN FLIGHT is left alone:
    the bank may be about to confirm it (reconcile_pending_attempts gives up
    on truly stuck ones). Returns the number cancelled.
    """
    now = _now()
    in_flight = select(PaymentAttempt.id).where(
        PaymentAttempt.booking_id == Booking.id, PaymentAttempt.status == "pending"
    ).exists()
    result = db.execute(
        update(Booking)
        .where(Booking.status == "pending_payment", Booking.payment_due_at < now, ~in_flight)
        .values(status="cancelled", cancelled_at=now)
        .returning(Booking.id)
    )
    expired_ids = [row[0] for row in result]
    if expired_ids:
        # BookingSeat.status is a denormalized copy - see app/models.py.
        db.execute(
            update(BookingSeat).where(BookingSeat.booking_id.in_(expired_ids)).values(status="cancelled")
        )
    db.commit()
    return len(expired_ids)
