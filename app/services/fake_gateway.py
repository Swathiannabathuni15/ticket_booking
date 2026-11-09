"""A SIMULATED BANK / payment provider. Replace this module with a real one
(Stripe, Razorpay, ...) and the rest of the app barely changes - it only uses
`charge`, `get_charge`, `refund`, and receives `payment.*` webhooks.

What a real provider guarantees, and this fake reproduces:
  1. IDEMPOTENT CHARGES. One idempotency key = at most one charge, ever.
     Calling `charge` again with the same key returns the first result and
     does NOT move money again. So a retry after a network glitch is safe.
  2. THE BANK COMMITS ON ITS OWN. Its records live in its own transaction
     (own DB session here). If our reply is lost, the money still moved.
  3. SIGNED WEBHOOKS. The bank tells us the outcome out-of-band, signed with
     a shared secret (HMAC-SHA256 over "<timestamp>.<body>"), so we confirm
     the booking even if the customer's phone died before the response.
     Banks RETRY webhooks, so receivers must tolerate duplicates.

Demo "cards" (payment_method): tok_ok = charges fine; tok_decline = card
declined; tok_timeout = the bank CHARGES but our reply is lost (the nasty case).
"""
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time

import httpx
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import GatewayCharge

log = logging.getLogger("gateway")


class GatewayTimeout(Exception):
    """We sent the charge but never saw the answer. The charge MAY have happened."""


class CardDeclined(Exception):
    def __init__(self, reason: str = "Card declined"):
        super().__init__(reason)
        self.reason = reason


# ---- the bank's API ------------------------------------------------------

def charge(idempotency_key: str, amount_cents: int, method: str) -> GatewayCharge:
    with SessionLocal() as bank_db:  # the bank's own transaction
        existing = bank_db.query(GatewayCharge).filter_by(idempotency_key=idempotency_key).one_or_none()
        if existing is not None:  # same key => same answer, no second charge
            if existing.status == "failed":
                raise CardDeclined(existing.failure_reason or "Card declined")
            return existing
        ok = method != "tok_decline"
        row = GatewayCharge(
            charge_id="ch_" + secrets.token_hex(10), idempotency_key=idempotency_key, amount_cents=amount_cents,
            status="succeeded" if ok else "failed", failure_reason=None if ok else "Your card was declined",
        )
        bank_db.add(row)
        try:
            bank_db.commit()
        except IntegrityError:  # two concurrent charges with one key: the bank keeps the first
            bank_db.rollback()
            first = bank_db.query(GatewayCharge).filter_by(idempotency_key=idempotency_key).one()
            if first.status == "failed":
                raise CardDeclined(first.failure_reason or "Card declined")
            return first
        bank_db.refresh(row)
        event = build_event(row)
    if not ok:
        schedule_webhook(event)
        raise CardDeclined(row.failure_reason or "Card declined")
    if method == "tok_timeout":
        schedule_webhook(event)       # the bank WILL tell us, even though this reply is lost
        raise GatewayTimeout("no response from the bank")
    if settings.gateway_always_send_webhook:
        schedule_webhook(event)
    return row


def get_charge(idempotency_key: str) -> GatewayCharge | None:
    with SessionLocal() as bank_db:
        return bank_db.query(GatewayCharge).filter_by(idempotency_key=idempotency_key).one_or_none()


def refund(idempotency_key: str) -> None:
    with SessionLocal() as bank_db:
        row = bank_db.query(GatewayCharge).filter_by(idempotency_key=idempotency_key).one_or_none()
        if row is not None and row.status == "succeeded":
            row.status = "refunded"
            bank_db.commit()


# ---- webhooks ------------------------------------------------------------

def build_event(charge_row: GatewayCharge) -> dict:
    return {
        "id": "evt_" + secrets.token_hex(10),
        "type": "payment.succeeded" if charge_row.status == "succeeded" else "payment.failed",
        "data": {"charge_id": charge_row.charge_id, "idempotency_key": charge_row.idempotency_key,
                 "amount_cents": charge_row.amount_cents, "status": charge_row.status,
                 "failure_reason": charge_row.failure_reason},
    }


def sign(body: bytes, timestamp: int | None = None, secret: str | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new((secret or settings.payment_webhook_secret).encode(), f"{ts}.".encode() + body, hashlib.sha256)
    return f"t={ts},v1={mac.hexdigest()}"


def verify(body: bytes, header: str | None) -> bool:
    """True only for a correct signature on a fresh (not replayed-later) request."""
    if not header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
        given = parts["v1"]
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > settings.payment_webhook_tolerance_seconds:
        return False
    expected = sign(body, ts).split("v1=")[1]
    return hmac.compare_digest(expected, given)


def deliver_webhook(event: dict) -> bool:
    """POST the signed event to our own webhook URL, retrying like a real bank."""
    body = json.dumps(event).encode()
    for attempt in range(4):
        try:
            r = httpx.post(settings.payment_webhook_url, content=body, timeout=5,
                           headers={"Content-Type": "application/json", "X-Gateway-Signature": sign(body)})
            if r.status_code < 300:
                return True
            log.warning("webhook %s got HTTP %s (attempt %s)", event["id"], r.status_code, attempt + 1)
        except httpx.HTTPError as exc:
            log.warning("webhook %s failed: %s (attempt %s)", event["id"], exc, attempt + 1)
        time.sleep(min(2 ** attempt, 8))
    return False


def schedule_webhook(event: dict) -> None:
    """Fire-and-forget delivery after a short delay. (Tests replace this.)"""
    def run():
        time.sleep(settings.gateway_webhook_delay_seconds)
        deliver_webhook(event)
    threading.Thread(target=run, daemon=True).start()
