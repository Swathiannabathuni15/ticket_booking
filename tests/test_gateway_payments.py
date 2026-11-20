"""Payments against the simulated bank: idempotency end to end, lost replies,
signed webhooks, duplicates, late payments, and the safety-net sweeper."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import Booking, GatewayCharge, Payment, PaymentAttempt
from app.services import fake_gateway
from app.services.payment_service import expire_unpaid_bookings, reconcile_pending_attempts
from tests.conftest import book_seat_pending, make_showtime_with_seats, signup_and_login


@pytest.fixture()
def bank_events(monkeypatch):
    """Capture the webhooks the bank would send (instead of a real HTTP call)."""
    events = []
    monkeypatch.setattr(fake_gateway, "schedule_webhook", events.append)
    return events


def _setup(client, email="a@example.com", seats=2):
    h = signup_and_login(client, email)
    st = make_showtime_with_seats(client, rows=1, seats_per_row=max(seats, 2), price=10.0)
    return h, [s["id"] for s in st["seats"]]


def _pay(client, h, booking_id, key, method="tok_ok"):
    return client.post(
        "/payments", json={"booking_id": booking_id, "payment_method": method}, headers={**h, "Idempotency-Key": key}
    )


def _webhook(client, event, body=None, header=None):
    raw = body if body is not None else json.dumps(event).encode()
    return client.post(
        "/webhooks/payments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Gateway-Signature": header if header is not None else fake_gateway.sign(raw),
        },
    )


def _status(client, h, booking_id):
    return next(b for b in client.get("/bookings/me", headers=h).json() if b["id"] == booking_id)


def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_declined_card_fails_cleanly_and_a_new_key_can_retry(client, db):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    r = _pay(client, h, b["id"], "key-decline-1", "tok_decline")
    assert r.status_code == 402 and "declined" in r.json()["detail"].lower()
    assert _status(client, h, b["id"])["status"] == "pending_payment"  # seat still yours
    assert _status(client, h, b["id"])["payment_status"] == "failed"
    # the SAME key replays the SAME failure without calling the bank again
    assert _pay(client, h, b["id"], "key-decline-1", "tok_ok").status_code == 402
    assert _pay(client, h, b["id"], "key-fresh-2", "tok_ok").status_code == 201
    assert _count(db, Payment) == 1


def test_lost_reply_then_webhook_confirms_the_booking(client, db, bank_events):
    """The bank charged, but our reply was lost (phone off, wifi gone)."""
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    r = _pay(client, h, b["id"], "key-lost-1", "tok_timeout")
    assert r.status_code == 202  # "processing"
    assert _status(client, h, b["id"])["payment_status"] == "processing"
    assert _count(db, GatewayCharge) == 1  # the money DID move
    assert len(bank_events) == 1 and bank_events[0]["type"] == "payment.succeeded"

    assert _webhook(client, bank_events[0]).json()["result"] == "processed"
    done = _status(client, h, b["id"])
    assert done["status"] == "confirmed" and done["payment_status"] == "paid"
    assert _count(db, Payment) == 1
    # the client comes back online and retries with its saved key: same answer, no new charge
    assert _pay(client, h, b["id"], "key-lost-1").status_code == 200
    assert _count(db, GatewayCharge) == 1


def test_retry_with_same_key_recovers_even_if_the_webhook_never_arrives(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    assert _pay(client, h, b["id"], "key-retry-1", "tok_timeout").status_code == 202
    r = _pay(client, h, b["id"], "key-retry-1")  # no webhook delivered at all
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert _count(db, GatewayCharge) == 1 and _count(db, Payment) == 1
    # ...and the late webhook is harmless
    assert _webhook(client, bank_events[0]).status_code == 200
    assert _count(db, Payment) == 1


def test_duplicate_webhook_delivery_is_harmless(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    _pay(client, h, b["id"], "key-dup-1", "tok_timeout")
    assert _webhook(client, bank_events[0]).json()["result"] == "processed"
    assert _webhook(client, bank_events[0]).json()["result"] == "duplicate"
    assert _count(db, Payment) == 1


def test_webhook_signature_is_enforced(client, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    _pay(client, h, b["id"], "key-sig-1", "tok_timeout")
    event = bank_events[0]
    raw = json.dumps(event).encode()
    good = fake_gateway.sign(raw)
    assert client.post("/webhooks/payments", content=raw).status_code == 400  # no signature
    assert _webhook(client, event, header="t=1,v1=abc").status_code == 400  # garbage
    assert (
        _webhook(client, event, body=raw.replace(b"succeeded", b"succeedex"), header=good).status_code == 400
    )  # tampered body
    old = int(time.time()) - settings.payment_webhook_tolerance_seconds - 60
    assert _webhook(client, event, header=fake_gateway.sign(raw, old)).status_code == 400  # replayed later
    wrong = fake_gateway.sign(raw, secret="not-the-secret")
    assert _webhook(client, event, header=wrong).status_code == 400  # wrong secret
    assert _webhook(client, event, body=b"not json", header=fake_gateway.sign(b"not json")).status_code == 400
    assert _webhook(client, event).status_code == 200  # the real one works


def test_payment_arriving_after_the_booking_was_cancelled_is_refunded(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    _pay(client, h, b["id"], "key-late-1", "tok_timeout")
    assert client.delete(f"/bookings/{b['id']}", headers=h).status_code == 200  # seat may be resold now
    assert _webhook(client, bank_events[0]).status_code == 200
    assert _status(client, h, b["id"])["status"] == "cancelled"  # NOT confirmed
    assert _count(db, Payment) == 0
    db.expire_all()
    assert db.scalar(select(GatewayCharge.status)) == "refunded"
    assert db.scalar(select(PaymentAttempt.status)) == "refunded"


def test_same_key_cannot_be_reused_for_another_booking(client):
    h, seats = _setup(client)
    b1 = book_seat_pending(client, h, seats[0])
    b2 = book_seat_pending(client, h, seats[1])
    assert _pay(client, h, b1["id"], "key-reuse-1").status_code == 201
    assert _pay(client, h, b2["id"], "key-reuse-1").status_code == 409


def test_second_payment_while_one_is_in_flight_is_refused(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    assert _pay(client, h, b["id"], "key-a-1", "tok_timeout").status_code == 202
    r = _pay(client, h, b["id"], "key-b-1")
    assert r.status_code == 409 and "already being processed" in r.json()["detail"]
    assert _count(db, GatewayCharge) == 1


def test_eight_simultaneous_requests_with_one_key_charge_once(client, db):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda _: _pay(client, h, b["id"], "key-race-1").status_code, range(8)))
    assert set(codes) <= {200, 201, 202}
    assert _count(db, GatewayCharge) == 1 and _count(db, Payment) == 1
    assert _status(client, h, b["id"])["status"] == "confirmed"


def test_sweeper_finishes_a_payment_whose_webhook_never_came(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    _pay(client, h, b["id"], "key-sweep-1", "tok_timeout")
    assert reconcile_pending_attempts(db) == 0  # too fresh to chase
    settings.payment_reconcile_after_seconds = 0
    try:
        assert reconcile_pending_attempts(db) == 1
    finally:
        settings.payment_reconcile_after_seconds = 60
    assert _status(client, h, b["id"])["status"] == "confirmed"


def test_unpaid_expiry_does_not_cancel_a_booking_with_a_payment_in_flight(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    _pay(client, h, b["id"], "key-exp-1", "tok_timeout")
    booking = db.get(Booking, b["id"])
    booking.payment_due_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert expire_unpaid_bookings(db) == 0
    assert _status(client, h, b["id"])["status"] == "pending_payment"


def test_webhook_for_an_unknown_payment_is_acknowledged(client):
    event = {
        "id": "evt_x1",
        "type": "payment.succeeded",
        "data": {"idempotency_key": "9999:nope", "charge_id": "ch_x", "amount_cents": 1},
    }
    assert _webhook(client, event).json()["result"] == "unknown"


def test_bank_failure_webhook_marks_the_attempt_failed(client, db, bank_events):
    h, seats = _setup(client)
    b = book_seat_pending(client, h, seats[0])
    _pay(client, h, b["id"], "key-fail-1", "tok_decline")
    assert bank_events and bank_events[0]["type"] == "payment.failed"
    assert _webhook(client, bank_events[0]).status_code == 200
    assert _status(client, h, b["id"])["payment_status"] == "failed"


def test_the_versioned_webhook_path_works_and_is_not_rate_limited(client):
    from app.config import settings

    settings.rate_limit_user_per_second = 1
    assert all(client.post("/api/v1/webhooks/payments", content=b"{}").status_code == 400 for _ in range(4))
