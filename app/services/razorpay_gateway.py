"""Razorpay (https://razorpay.com), used in TEST MODE with free test keys: no real money moves.

THE FLOW (Razorpay "Standard Checkout")
  1. Our server creates an ORDER for the exact amount (taken from our database, in paise) and saves the
     order id on the payment attempt BEFORE the customer sees anything.
  2. The website opens Razorpay's payment window for that order id. The customer pays there (card, UPI,
     net banking: Razorpay's page, not ours, so we never see card details).
  3. We hear about the result TWO ways, and both end in the same repeat-safe step (_settle_success):
       a. the browser sends back order id + payment id + a signature, which only Razorpay's secret can
          produce (check_signature);
       b. Razorpay calls our webhook, signed with the webhook secret (verify_webhook), even if the
          customer's phone died before (a).
     A third path, the sweeper, asks Razorpay for the order's payments (order_payments).

WHAT IS AND IS NOT TRUSTED
  - The amount always comes from OUR database. The order is created for it, and a webhook whose amount
    differs from the attempt is refused.
  - A browser saying "I paid" counts only with a valid signature over (order id | payment id).
  - A webhook counts only with a valid signature over the raw body. Razorpay's webhook signature has NO
    timestamp, so replay protection is the event id: each event is processed once (webhook_events).

NOT DONE HERE: Razorpay can leave a payment "authorized" until it is "captured". Turn on AUTOMATIC
CAPTURE in the Razorpay dashboard (Settings > Configuration > Payment Capture), or money stays on hold.

Secrets (key secret, webhook secret) come from settings (.env) and are never logged.
"""

import hashlib
import hmac
import logging

import httpx

from app.config import settings

log = logging.getLogger("razorpay")
CURRENCY = "INR"
TIMEOUT_SECONDS = 15


class GatewayUnavailable(Exception):
    """Razorpay could not be reached, or answered with an error. The caller may retry."""


class NotConfigured(Exception):
    """PAYMENT_PROVIDER=razorpay but the keys are missing."""


def configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def _request(method: str, path: str, json: dict | None = None) -> dict:
    """One call to Razorpay's API. (Tests replace this function with an in-memory fake.)"""
    if not configured():
        raise NotConfigured()
    try:
        r = httpx.request(
            method,
            settings.razorpay_api_base.rstrip("/") + path,
            json=json,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GatewayUnavailable("could not reach Razorpay") from exc
    if r.status_code >= 400:
        # Razorpay's error text is safe and useful ("The amount must be at least 100"); our keys are not in it.
        try:
            why = r.json().get("error", {}).get("description", "")
        except ValueError:
            why = ""
        log.warning("razorpay %s %s -> HTTP %s: %s", method, path, r.status_code, why[:200])
        raise GatewayUnavailable(f"Razorpay answered HTTP {r.status_code}")
    return r.json()


# ---- calls ----------------------------------------------------------------


def create_order(amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> dict:
    """A new order. `receipt` (max 40 chars) is our own reference and shows up in Razorpay's dashboard."""
    return _request(
        "POST",
        "/orders",
        {"amount": amount_paise, "currency": CURRENCY, "receipt": receipt[:40], "notes": notes or {}},
    )


def order_payments(order_id: str) -> list[dict]:
    """Every payment try made against an order (failed ones included)."""
    return _request("GET", f"/orders/{order_id}/payments").get("items", [])


def fetch_payment(payment_id: str) -> dict:
    return _request("GET", f"/payments/{payment_id}")


def refund(payment_id: str, amount_paise: int) -> dict:
    return _request("POST", f"/payments/{payment_id}/refund", {"amount": amount_paise})


# ---- signatures -----------------------------------------------------------


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def check_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """The signature the payment window hands the browser: HMAC-SHA256 of "<order id>|<payment id>" with
    the KEY SECRET. Only Razorpay (and we) can make it, so a forged "I paid" fails."""
    if not (order_id and payment_id and signature and settings.razorpay_key_secret):
        return False
    expected = _hmac_hex(settings.razorpay_key_secret, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook(body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 of the raw request body with the WEBHOOK SECRET, sent as X-Razorpay-Signature."""
    if not signature or not settings.razorpay_webhook_secret:
        return False
    return hmac.compare_digest(_hmac_hex(settings.razorpay_webhook_secret, body), signature)
