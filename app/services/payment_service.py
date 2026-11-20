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
         a. WEBHOOK: the bank calls POST /api/v1/webhooks/payments, signed, whether or
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
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import required
from app.models import Booking, BookingSeat, Payment, PaymentAttempt, WebhookEvent
from app.services import fake_gateway, razorpay_gateway, ticket_email, ticket_service
from app.services.split_service import booking_total_cents

log = logging.getLogger(__name__)


class PaymentError(Exception):
    """Base of the "this payment can't go ahead" answers below."""


class BookingNotFound(PaymentError):
    pass


class NotYourBooking(PaymentError):
    pass


class AlreadyPaid(PaymentError):
    pass


class BookingNotPayable(PaymentError):
    """Cancelled or expired - there is nothing left to pay for."""


class PaymentDeclined(PaymentError):
    pass


class PaymentInProgress(PaymentError):
    """Another attempt (different key) for this booking is still being processed."""


class BadSignature(PaymentError):
    """The "I paid" message from the browser did not carry a valid Razorpay signature."""


class WrongProvider(PaymentError):
    """This kind of payment is switched off (e.g. the fake card endpoint while Razorpay is the provider)."""


class KeyReused(PaymentError):
    """The same idempotency key was used for a different booking."""


def _now() -> datetime:
    return datetime.now(UTC)


def _find_attempt(db: Session, user_id: int, key: str) -> PaymentAttempt | None:
    return db.scalar(
        select(PaymentAttempt).where(PaymentAttempt.user_id == user_id, PaymentAttempt.idempotency_key == key)
    )


def _start_attempt(
    db: Session, user_id: int, booking_id: int, key: str, provider: str = "simulated"
) -> tuple[PaymentAttempt, bool]:
    """Validate and record a new pending attempt. Returns (attempt, is_new)."""
    try:
        booking = db.scalar(
            select(Booking).where(Booking.id == booking_id).with_for_update().execution_options(populate_existing=True)
        )
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.user_id != user_id:
            raise NotYourBooking(booking_id)
        if booking.status == "confirmed":
            raise AlreadyPaid(booking_id)
        if booking.status != "pending_payment":
            raise BookingNotPayable(booking_id)
        same_key = _find_attempt(db, user_id, key)  # a concurrent request with this key got here first
        if same_key is not None:
            db.commit()
            return same_key, False
        in_flight = db.scalar(
            select(PaymentAttempt.id).where(PaymentAttempt.booking_id == booking_id, PaymentAttempt.status == "pending")
        )
        if in_flight is not None:
            raise PaymentInProgress(booking_id)
        attempt = PaymentAttempt(
            booking_id=booking_id,
            user_id=user_id,
            idempotency_key=key,
            gateway_key=f"{user_id}:{key}",
            provider=provider,
            amount_cents=booking_total_cents(db, booking_id),
            status="pending",
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
    attempt = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is not None and attempt.status == "pending":
        attempt.status = "failed"
        attempt.failure_reason = reason[:200]
    db.commit()


def _refund(provider: str, gateway_key: str, payment_id: str, amount_cents: int) -> None:
    """Give the money back through whoever took it. A Razorpay refund that fails now (network) is NOT lost:
    the attempt is already marked refunded, and the sweeper re-checks those and retries."""
    if provider == "razorpay":
        try:
            razorpay_gateway.refund(payment_id, amount_cents)
        except (razorpay_gateway.GatewayUnavailable, razorpay_gateway.NotConfigured):
            log.error("refund of %s did not go through now; the sweeper will retry", payment_id)
    else:
        fake_gateway.refund(gateway_key)


# NOTE ON LOCKED READS. Every `with_for_update()` select here also says populate_existing: after waiting for
# a row lock, the session must re-read the row. Without it SQLAlchemy hands back the copy it loaded BEFORE
# the wait, which another request may have changed in the meantime (that made two simultaneous requests
# each create a Razorpay order, and could make a late duplicate "paid" message refund a good payment).


def _settle_success(db: Session, attempt_id: int, charge_id: str) -> Booking:
    """The bank says the money moved. Confirm the booking - or refund if that
    is no longer possible. Safe to call any number of times, from anywhere."""
    refund_args: tuple[str, str, str, int] | None = None
    just_confirmed = False
    try:
        booking_id = db.scalar(select(PaymentAttempt.booking_id).where(PaymentAttempt.id == attempt_id))
        # Same lock order everywhere (booking, then attempt) so two paths can't deadlock.
        booking = required(
            db.scalar(
                select(Booking)
                .where(Booking.id == booking_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
            "booking",
        )
        attempt = required(
            db.scalar(
                select(PaymentAttempt)
                .where(PaymentAttempt.id == attempt_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
            "attempt",
        )
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
            ticket_service.issue_ticket(db, booking)  # the QR e-ticket, in the same transaction
            just_confirmed = True
        else:
            attempt.status = "refunded"
            attempt.failure_reason = f"paid, but the booking was already {booking.status}"
            refund_args = (attempt.provider, attempt.gateway_key, charge_id, attempt.amount_cents)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if refund_args:
        _refund(*refund_args)
        log.warning("refunded charge %s: booking %s was not payable", charge_id, booking_id)
    if just_confirmed:
        ticket_email.notify(booking.id)  # best effort, after commit; the sweeper retries
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
    if settings.payment_provider != "simulated":
        # With a real provider the fake card must be OFF, or anyone could "pay" with tok_ok for free.
        raise WrongProvider(settings.payment_provider)
    attempt = _find_attempt(db, user_id, idempotency_key)
    is_new = False
    if attempt is None:
        attempt, is_new = _start_attempt(db, user_id, booking_id, idempotency_key)
    if attempt.booking_id != booking_id:
        raise KeyReused(idempotency_key)

    if not is_new:
        if attempt.status == "succeeded":
            return required(db.get(Booking, booking_id), "booking"), "replayed"
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
        return required(db.get(Booking, booking_id), "booking"), "processing"
    return _settle_success(db, attempt.id, charge.charge_id), "created"


# ------------------------------------------------------------------ Razorpay


def start_razorpay_order(db: Session, user_id: int, booking_id: int, key: str) -> tuple[PaymentAttempt, dict]:
    """Step 1 of paying with Razorpay: make sure this booking has ONE order, saved in our database, and
    return it for the payment window. Safe to call again and again (a double click, a reload, a second
    tab): it hands back the SAME order, never a second one.

    Returns (attempt, order) where order = {"id", "amount" (paise), "currency"}."""
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise BookingNotFound(booking_id)
    if booking.user_id != user_id:
        raise NotYourBooking(booking_id)
    if booking.status == "confirmed":
        raise AlreadyPaid(booking_id)
    if booking.status != "pending_payment":
        raise BookingNotPayable(booking_id)

    attempt = _find_attempt(db, user_id, key)
    if attempt is not None and attempt.booking_id != booking_id:
        raise KeyReused(key)
    if attempt is None:  # one open order per booking, whatever key the browser sends this time
        attempt = db.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.booking_id == booking_id,
                PaymentAttempt.status == "pending",
                PaymentAttempt.provider == "razorpay",
            )
        )
    if attempt is None:
        attempt, _ = _start_attempt(db, user_id, booking_id, key, provider="razorpay")
    if attempt.status != "pending":
        raise PaymentDeclined("The earlier payment attempt did not complete. Nothing was charged. Please try again.")

    try:
        # Lock the attempt while we create the order: two requests at the same instant must not make two.
        locked = required(
            db.scalar(
                select(PaymentAttempt)
                .where(PaymentAttempt.id == attempt.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
            "attempt",
        )
        if locked.gateway_order_id is None:
            order = razorpay_gateway.create_order(
                locked.amount_cents,
                receipt=f"bk{booking_id}-at{locked.id}",
                notes={"booking_id": str(booking_id), "attempt_id": str(locked.id)},
            )
            locked.gateway_order_id = order["id"]
        db.commit()
    except Exception:
        db.rollback()
        raise
    return locked, {"id": locked.gateway_order_id, "amount": locked.amount_cents, "currency": razorpay_gateway.CURRENCY}


def confirm_razorpay_payment(db: Session, user_id: int, order_id: str, payment_id: str, signature: str) -> Booking:
    """Step 3a: the browser reports "paid". Counts only with a valid signature. Then the same repeat-safe
    step the webhook uses, so it does not matter which of the two arrives first, or if both do."""
    attempt = db.scalar(select(PaymentAttempt).where(PaymentAttempt.gateway_order_id == order_id))
    if attempt is None:
        raise BookingNotFound(order_id)
    if attempt.user_id != user_id:
        raise NotYourBooking(order_id)
    if not razorpay_gateway.check_signature(order_id, payment_id, signature):
        raise BadSignature(order_id)
    return _settle_success(db, attempt.id, payment_id)


def handle_razorpay_webhook(db: Session, event_id: str, event: dict) -> str:
    """Step 3b: one signed Razorpay event. Returns "processed" | "duplicate" | "unknown" | "ignored" |
    "amount_mismatch". The event row and its effects commit together, so if anything fails the event is
    NOT recorded and Razorpay's retry is processed properly."""
    try:
        db.add(WebhookEvent(event_id=f"rzp:{event_id}"[:80], type=str(event.get("event", "?"))[:60]))
        db.flush()
    except IntegrityError:
        db.rollback()
        return "duplicate"
    try:
        payload = event.get("payload") or {}
        payment = (payload.get("payment") or {}).get("entity") or {}
        order_id = payment.get("order_id") or ((payload.get("order") or {}).get("entity") or {}).get("id")
        attempt = (
            db.scalar(select(PaymentAttempt).where(PaymentAttempt.gateway_order_id == order_id)) if order_id else None
        )
        if attempt is None:
            db.commit()
            return "unknown"
        kind = event.get("event")
        if kind in ("payment.captured", "order.paid") and payment.get("id"):
            if (
                payment.get("amount") != attempt.amount_cents
                or payment.get("currency", "INR") != razorpay_gateway.CURRENCY
            ):
                # Never confirm a booking for a payment of a different amount than we asked for.
                log.error(
                    "webhook amount mismatch on order %s: got %s, expected %s",
                    order_id,
                    payment.get("amount"),
                    attempt.amount_cents,
                )
                db.commit()
                return "amount_mismatch"
            _settle_success(db, attempt.id, payment["id"])  # commits the event row too
            return "processed"
        if kind == "payment.failed":
            # NOT final: the customer can try another card on the same order. Just remember why.
            if attempt.status == "pending":
                attempt.failure_reason = str(payment.get("error_description") or "Payment failed")[:200]
            db.commit()
            return "processed"
        db.commit()
        return "ignored"
    except Exception:
        db.rollback()
        raise


def _reconcile_razorpay(db: Session, a: PaymentAttempt, now: datetime) -> bool:
    """One old pending Razorpay attempt: ask Razorpay what really happened to its order."""
    if a.gateway_order_id:
        try:
            payments = razorpay_gateway.order_payments(a.gateway_order_id)
        except (razorpay_gateway.GatewayUnavailable, razorpay_gateway.NotConfigured):
            return False  # try again next round
        captured = next((p for p in payments if p.get("status") == "captured"), None)
        if captured:
            _settle_success(db, a.id, captured["id"])
            return True
    if a.created_at < now - timedelta(seconds=settings.payment_give_up_after_seconds):
        _mark_failed(db, a.id, "no payment was made")
        return True
    return False


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
    pending = db.scalars(
        select(PaymentAttempt).where(
            PaymentAttempt.status == "pending",
            PaymentAttempt.created_at < now - timedelta(seconds=settings.payment_reconcile_after_seconds),
        )
    ).all()
    for a in pending:
        if a.provider == "razorpay":
            fixed += _reconcile_razorpay(db, a, now)
            continue
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
        if a.provider == "razorpay":
            # Only recent ones: each check is a call to Razorpay.
            if a.charge_id and a.updated_at > now - timedelta(days=1):
                try:
                    if razorpay_gateway.fetch_payment(a.charge_id).get("status") == "captured":  # not refunded yet
                        razorpay_gateway.refund(a.charge_id, a.amount_cents)
                        fixed += 1
                except (razorpay_gateway.GatewayUnavailable, razorpay_gateway.NotConfigured):
                    continue
            continue
        charge = fake_gateway.get_charge(a.gateway_key)
        if charge is not None and charge.status == "succeeded":
            fake_gateway.refund(a.gateway_key)
            fixed += 1
    return fixed


def expire_unpaid_bookings(db: Session) -> int:
    """Cancel every pending_payment booking whose payment_due_at has passed,
    freeing the seat. A booking with a payment still IN FLIGHT is left alone:
    the bank may be about to confirm it (reconcile_pending_attempts gives up
    on truly stuck ones). Only the simulated bank has such a thing: a pending Razorpay attempt just means
    "the payment window was opened". If the customer does pay after the booking expired, the money is
    refunded automatically (_settle_success). Returns the number cancelled.
    """
    now = _now()
    in_flight = (
        select(PaymentAttempt.id)
        .where(
            PaymentAttempt.booking_id == Booking.id,
            PaymentAttempt.status == "pending",
            PaymentAttempt.provider == "simulated",
        )
        .exists()
    )
    result = db.execute(
        update(Booking)
        .where(Booking.status == "pending_payment", Booking.payment_due_at < now, ~in_flight)
        .values(status="cancelled", cancelled_at=now)
        .returning(Booking.id)
    )
    expired_ids = [row[0] for row in result]
    if expired_ids:
        # BookingSeat.status is a denormalized copy - see app/models.py.
        db.execute(update(BookingSeat).where(BookingSeat.booking_id.in_(expired_ids)).values(status="cancelled"))
    db.commit()
    return len(expired_ids)
