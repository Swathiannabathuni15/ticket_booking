"""Razorpay (test mode) payments: one order per attempt saved in the database, signed browser confirmation,
signed webhooks de-duplicated by event id, refunds for late payments, and the sweeper.

Razorpay's API is replaced by an in-memory fake (FakeRazorpay): nothing here touches the network."""

import hashlib
import hmac
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.models import Booking, PaymentAttempt, WebhookEvent
from app.services import payment_service as svc
from app.services import razorpay_gateway as rzp
from tests.conftest import book_seat_pending, make_showtime_with_seats, signup_and_login

KEY_SECRET, WEBHOOK_SECRET = "test-key-secret", "test-webhook-secret"


class FakeRazorpay:
    """Just enough of Razorpay's API: orders, the payments made on them, and refunds."""

    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.payments: dict[str, dict] = {}
        self.refunds: list[tuple[str, int]] = []
        self.down = False
        self.lock = threading.Lock()

    def request(self, method, path, json=None):
        if self.down:
            raise rzp.GatewayUnavailable("down")
        with self.lock:
            if method == "POST" and path == "/orders":
                oid = f"order_{len(self.orders) + 1:04d}"
                self.orders[oid] = {"id": oid, "amount": json["amount"], "currency": json["currency"]}
                return self.orders[oid]
            if method == "GET" and path.endswith("/payments") and path.startswith("/orders/"):
                oid = path.split("/")[2]
                return {"items": [p for p in self.payments.values() if p["order_id"] == oid]}
            if method == "GET" and path.startswith("/payments/"):
                return self.payments[path.split("/")[2]]
            if method == "POST" and path.endswith("/refund"):
                pid = path.split("/")[2]
                self.refunds.append((pid, json["amount"]))
                self.payments[pid]["status"] = "refunded"
                return {"id": "rfnd_1"}
        raise AssertionError(f"unexpected Razorpay call {method} {path}")

    def pay(self, order_id, status="captured", amount=None) -> dict:
        """The customer pays on Razorpay's page."""
        pid = f"pay_{len(self.payments) + 1:04d}"
        order = self.orders[order_id]
        self.payments[pid] = {
            "id": pid,
            "order_id": order_id,
            "amount": amount or order["amount"],
            "currency": "INR",
            "status": status,
        }
        return self.payments[pid]


@pytest.fixture()
def bank(monkeypatch):
    saved = {
        k: getattr(settings, k)
        for k in (
            "payment_provider",
            "razorpay_key_id",
            "razorpay_key_secret",
            "razorpay_webhook_secret",
            "payment_give_up_after_seconds",
        )
    }
    settings.payment_provider = "razorpay"
    settings.razorpay_key_id, settings.razorpay_key_secret = "rzp_test_abc", KEY_SECRET
    settings.razorpay_webhook_secret = WEBHOOK_SECRET
    fake = FakeRazorpay()
    monkeypatch.setattr(rzp, "_request", fake.request)
    yield fake
    for k, v in saved.items():
        setattr(settings, k, v)


def _sig(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _webhook(client, event: dict, event_id="evt_1", secret=WEBHOOK_SECRET):
    raw = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id}
    )


def _captured_event(payment: dict, kind="payment.captured") -> dict:
    return {"event": kind, "payload": {"payment": {"entity": payment}}}


def _booking(client, email="fan@example.com", price=250.0):
    h = signup_and_login(client, email)
    seat = make_showtime_with_seats(client, rows=1, seats_per_row=2, price=price)["seats"][0]["id"]
    return h, book_seat_pending(client, h, seat)["id"]


def _order(client, h, booking_id, key="k1"):
    return client.post(
        "/api/v1/payments/razorpay/order", json={"booking_id": booking_id}, headers={**h, "Idempotency-Key": key}
    )


# ---------- step 1: the order ----------


def test_order_is_created_for_the_database_amount_and_saved(client, db, bank):
    h, bid = _booking(client, price=250)
    r = _order(client, h, bid)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["amount"] == 25000 and o["currency"] == "INR" and o["key_id"] == "rzp_test_abc"  # paise, from OUR price
    assert "secret" not in r.text.lower()
    assert bank.orders[o["order_id"]]["amount"] == 25000
    a = db.query(PaymentAttempt).one()
    assert a.gateway_order_id == o["order_id"] and a.provider == "razorpay" and a.status == "pending"


def test_asking_again_never_creates_a_second_order(client, db, bank):
    h, bid = _booking(client)
    first = _order(client, h, bid, "k1").json()["order_id"]
    assert _order(client, h, bid, "k1").json()["order_id"] == first  # a reload / double click
    assert _order(client, h, bid, "another-key").json()["order_id"] == first  # even with a new key
    assert len(bank.orders) == 1 and db.query(PaymentAttempt).count() == 1


def test_simultaneous_requests_make_exactly_one_order(client, bank):
    h, bid = _booking(client)
    out, barrier = [], threading.Barrier(5)

    def go():
        barrier.wait()
        out.append(_order(client, h, bid).json().get("order_id"))

    ts = [threading.Thread(target=go) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(bank.orders) == 1 and set(out) == {"order_0001"}


def test_order_rules(client, bank):
    h, bid = _booking(client)
    other = signup_and_login(client, "other@example.com")
    assert _order(client, other, bid).status_code == 403
    assert _order(client, h, 9999).status_code == 404
    assert (
        client.post("/api/v1/payments/razorpay/order", json={"booking_id": bid}, headers=h).status_code == 422
    )  # no key
    client.delete(f"/api/v1/bookings/{bid}", headers=h)
    assert _order(client, h, bid, "k2").status_code == 409  # cancelled: nothing to pay


def test_razorpay_being_down_is_a_502_and_leaves_no_order_behind(client, db, bank):
    h, bid = _booking(client)
    bank.down = True
    assert _order(client, h, bid).status_code == 502
    assert db.query(PaymentAttempt).one().gateway_order_id is None
    bank.down = False
    assert _order(client, h, bid).status_code == 200  # the same attempt carries on


def test_not_configured_is_a_clear_503(client, bank):
    settings.razorpay_key_secret = ""
    h, bid = _booking(client)
    assert _order(client, h, bid).status_code == 503


def test_the_fake_card_is_switched_off_when_razorpay_is_the_provider(client, bank):
    """Otherwise anyone could pay with the demo 'tok_ok' card and get free tickets."""
    h, bid = _booking(client)
    r = client.post("/api/v1/payments", json={"booking_id": bid}, headers={**h, "Idempotency-Key": "free"})
    assert r.status_code == 400
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "pending_payment"
    cfg = client.get("/api/v1/config").json()
    assert cfg == {"test_cards": False, "payment_provider": "razorpay"}


def test_a_pending_razorpay_attempt_is_unpaid_not_processing_and_does_not_block_expiry(client, db, bank):
    h, bid = _booking(client)
    _order(client, h, bid)
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["payment_status"] == "unpaid"  # Pay button stays
    b = db.get(Booking, bid)
    b.payment_due_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert svc.expire_unpaid_bookings(db) == 1  # an abandoned payment window must not hold seats forever


# ---------- step 3a: the browser says "paid" ----------


def test_valid_signature_confirms_the_booking_and_issues_the_ticket(client, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    pay = bank.pay(oid)
    r = client.post(
        "/api/v1/payments/razorpay/verify",
        headers=h,
        json={
            "razorpay_order_id": oid,
            "razorpay_payment_id": pay["id"],
            "razorpay_signature": _sig(oid, pay["id"]),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["payment_status"] == "paid"
    assert client.get(f"/api/v1/bookings/{bid}/ticket", headers=h).json()["state"] == "valid"


def test_forged_or_wrong_signature_is_refused(client, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    for bad in (_sig(oid, "pay_x", "wrong-secret"), _sig(oid, "pay_other"), "deadbeef"):
        r = client.post(
            "/api/v1/payments/razorpay/verify",
            headers=h,
            json={
                "razorpay_order_id": oid,
                "razorpay_payment_id": "pay_x",
                "razorpay_signature": bad,
            },
        )
        assert r.status_code == 400, bad
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "pending_payment"


def test_someone_elses_order_cannot_be_confirmed(client, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    other = signup_and_login(client, "other@example.com")
    r = client.post(
        "/api/v1/payments/razorpay/verify",
        headers=other,
        json={
            "razorpay_order_id": oid,
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": _sig(oid, "pay_1"),
        },
    )
    assert r.status_code == 403


# ---------- step 3b: the webhook ----------


def test_webhook_confirms_even_if_the_browser_never_reports(client, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    r = _webhook(client, _captured_event(bank.pay(oid)))
    assert r.status_code == 200 and r.json()["result"] == "processed"
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "confirmed"


def test_webhook_needs_a_valid_signature(client, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    event = _captured_event(bank.pay(oid))
    assert _webhook(client, event, secret="not-the-secret").status_code == 400
    assert client.post("/api/v1/webhooks/razorpay", json=event).status_code == 400  # no signature at all
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "pending_payment"


def test_a_redelivered_webhook_is_harmless_and_both_paths_can_arrive(client, db, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    pay = bank.pay(oid)
    event = _captured_event(pay)
    assert _webhook(client, event, "evt_A").json()["result"] == "processed"
    assert _webhook(client, event, "evt_A").json()["result"] == "duplicate"  # same event id: Razorpay retried
    assert _webhook(client, event, "evt_B").status_code == 200  # a different event for the same payment: no harm
    v = client.post(
        "/api/v1/payments/razorpay/verify",
        headers=h,
        json={
            "razorpay_order_id": oid,
            "razorpay_payment_id": pay["id"],
            "razorpay_signature": _sig(oid, pay["id"]),
        },
    )
    assert v.status_code == 200 and v.json()["status"] == "confirmed"
    assert db.query(WebhookEvent).filter(WebhookEvent.event_id == "rzp:evt_A").count() == 1
    assert bank.refunds == []  # paying once, however many messages, is never refunded
    assert client.get(f"/api/v1/bookings/{bid}/ticket", headers=h).status_code == 200


def test_a_payment_of_the_wrong_amount_does_not_confirm(client, bank):
    h, bid = _booking(client, price=250)
    oid = _order(client, h, bid).json()["order_id"]
    r = _webhook(client, _captured_event(bank.pay(oid, amount=100)))
    assert r.json()["result"] == "amount_mismatch"
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "pending_payment"


def test_failed_payment_event_is_not_final_and_a_retry_on_the_same_order_works(client, db, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    failed = bank.pay(oid, status="failed")
    failed["error_description"] = "Card declined"
    assert _webhook(client, _captured_event(failed, "payment.failed"), "evt_f").json()["result"] == "processed"
    a = db.query(PaymentAttempt).one()
    assert (
        a.status == "pending" and a.failure_reason == "Card declined"
    )  # still open: the customer can try another card
    assert _webhook(client, _captured_event(bank.pay(oid)), "evt_ok").json()["result"] == "processed"
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "confirmed"


def test_unknown_order_and_other_events_are_acknowledged_and_ignored(client, bank):
    ghost = {"id": "pay_1", "order_id": "order_nope", "amount": 100, "currency": "INR", "status": "captured"}
    assert _webhook(client, _captured_event(ghost), "e1").json()["result"] == "unknown"
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    assert _webhook(client, _captured_event(bank.pay(oid), "refund.processed"), "e2").json()["result"] == "ignored"
    assert _webhook(client, {"nonsense": True}, "e3").status_code == 200


# ---------- money arriving late, and the sweeper ----------


def test_payment_after_the_booking_expired_is_refunded(client, db, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    b = db.get(Booking, bid)
    b.payment_due_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    svc.expire_unpaid_bookings(db)
    pay = bank.pay(oid)  # the customer was still in the payment window
    assert _webhook(client, _captured_event(pay)).json()["result"] == "processed"
    assert bank.refunds == [(pay["id"], 25000)]
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "cancelled"
    assert db.query(PaymentAttempt).one().status == "refunded"


def test_a_refund_that_fails_now_is_retried_by_the_sweeper(client, db, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    b = db.get(Booking, bid)
    b.payment_due_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    svc.expire_unpaid_bookings(db)
    pay = bank.pay(oid)
    original = bank.request

    def flaky(method, path, json=None):
        if path.endswith("/refund"):
            raise rzp.GatewayUnavailable("down")
        return original(method, path, json)

    rzp._request = flaky  # (the `bank` fixture restores the real function afterwards)
    _webhook(client, _captured_event(pay))
    assert bank.refunds == []  # Razorpay was unreachable: nothing refunded yet...
    rzp._request = original
    db.query(PaymentAttempt).update({"updated_at": datetime.now(UTC)})
    db.commit()
    assert svc.reconcile_pending_attempts(db) == 1  # ...but the sweeper notices and does it
    assert bank.refunds == [(pay["id"], 25000)]
    assert svc.reconcile_pending_attempts(db) == 0  # and only once


def _age(db, seconds):
    db.query(PaymentAttempt).update({"created_at": datetime.now(UTC) - timedelta(seconds=seconds)})
    db.commit()


def test_sweeper_confirms_a_payment_whose_webhook_never_came(client, db, bank):
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    bank.pay(oid)
    assert svc.reconcile_pending_attempts(db) == 0  # too young: the webhook may still be on its way
    _age(db, settings.payment_reconcile_after_seconds + 5)
    assert svc.reconcile_pending_attempts(db) == 1
    assert client.get("/api/v1/bookings/me", headers=h).json()[0]["status"] == "confirmed"


def test_sweeper_gives_up_on_an_order_nobody_paid_and_survives_razorpay_being_down(client, db, bank):
    h, bid = _booking(client)
    _order(client, h, bid)
    _age(db, settings.payment_give_up_after_seconds + 5)
    bank.down = True
    assert svc.reconcile_pending_attempts(db) == 0  # cannot ask: must NOT guess "unpaid"
    assert db.query(PaymentAttempt).one().status == "pending"
    bank.down = False
    assert svc.reconcile_pending_attempts(db) == 1
    a = db.query(PaymentAttempt).one()
    assert a.status == "failed"
    # the customer's next try on the same key is told, and then starts a fresh attempt
    assert _order(client, h, bid, "k1").status_code == 402
    assert _order(client, h, bid, "k2").status_code == 200


def test_browser_and_webhook_arriving_at_the_same_instant_confirm_once_and_never_refund(client, db, bank):
    """The nasty race: two paths settle the same payment together. The loser must see "already paid" (after
    waiting for the lock), NOT conclude the booking was unpayable and refund a good payment."""
    h, bid = _booking(client)
    oid = _order(client, h, bid).json()["order_id"]
    pay = bank.pay(oid)
    verify = {"razorpay_order_id": oid, "razorpay_payment_id": pay["id"], "razorpay_signature": _sig(oid, pay["id"])}
    barrier, codes = threading.Barrier(6), []

    def browser():
        barrier.wait()
        codes.append(client.post("/api/v1/payments/razorpay/verify", headers=h, json=verify).status_code)

    def hook(n):
        barrier.wait()
        codes.append(_webhook(client, _captured_event(pay), f"evt_{n}").status_code)

    ts = [threading.Thread(target=browser) for _ in range(3)] + [
        threading.Thread(target=hook, args=(n,)) for n in range(3)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert codes == [200] * 6
    assert bank.refunds == []
    db.expire_all()
    assert db.get(Booking, bid).status == "confirmed" and db.query(PaymentAttempt).one().status == "succeeded"


# ---------- configuration safety ----------


def test_production_refuses_the_fake_bank_and_missing_razorpay_keys():
    from app.config import Settings

    base = dict(
        environment="production",
        jwt_secret="x" * 48,
        payment_webhook_secret="y" * 48,
        otp_dev_mode=False,
        cors_origin_regex="",
        gateway_allow_test_methods=False,
        public_base_url="https://t.example.com",
    )
    fake_bank = Settings(_env_file=None, **base).production_problems()
    assert any("simulated" in p and "free" in p for p in fake_bank)
    no_keys = Settings(_env_file=None, payment_provider="razorpay", **base).production_problems()
    assert any("RAZORPAY_KEY_ID" in p for p in no_keys)
    ok = Settings(
        _env_file=None,
        payment_provider="razorpay",
        razorpay_key_id="rzp_test_1",
        razorpay_key_secret="s",
        razorpay_webhook_secret="w",
        **base,
    ).production_problems()
    assert ok == []
