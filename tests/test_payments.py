"""Fake payments: two-step booking, idempotent retries, double-payment
rejection, and expiry of an unpaid booking (see app/services/payment_service.py).
"""
from datetime import datetime, timedelta, timezone

import redis

from app.jobs.queue import get_queue
from app.jobs.tasks import expire_unpaid_bookings_job
from app.models import Booking
from app.services.payment_service import expire_unpaid_bookings
from tests.conftest import TEST_REDIS_DB, admin_headers, book_seat_pending, signup_and_login

EVENT = {
    "name": "E", "venue": "V", "starts_at": "2030-01-01T18:00:00+09:00",
    "rows": 1, "seats_per_row": 2, "price": 10.0,
}


def _make_event_with_seats(client) -> int:
    event = client.post("/events", json=EVENT, headers=admin_headers(client)).json()
    return event["id"]


def test_booking_starts_as_pending_payment(client):
    h = signup_and_login(client, "a@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]

    booking = book_seat_pending(client, h, seat_id)
    assert booking["status"] == "pending_payment"
    assert booking["payment_due_at"] is not None
    assert booking["paid_at"] is None


def test_paying_confirms_the_booking(client):
    h = signup_and_login(client, "a@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h, seat_id)

    r = client.post(
        "/payments", json={"booking_id": booking["id"]}, headers={**h, "Idempotency-Key": "pay-1"}
    )
    assert r.status_code == 201
    assert r.json()["status"] == "confirmed"
    assert r.json()["paid_at"] is not None


def test_payment_retry_with_same_key_does_not_charge_twice(client, db):
    h = signup_and_login(client, "a@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h, seat_id)
    headers = {**h, "Idempotency-Key": "pay-retry"}

    first = client.post("/payments", json={"booking_id": booking["id"]}, headers=headers)
    retry = client.post("/payments", json={"booking_id": booking["id"]}, headers=headers)

    assert first.status_code == 201
    assert retry.status_code == 200
    # Same booking, same moment - compared as datetimes since Postgres can
    # format the identical instant with a different UTC offset on each read.
    first_body, retry_body = first.json(), retry.json()
    assert first_body["id"] == retry_body["id"]
    assert first_body["status"] == retry_body["status"] == "confirmed"
    assert datetime.fromisoformat(first_body["paid_at"]) == datetime.fromisoformat(retry_body["paid_at"])

    from app.models import Payment
    assert db.query(Payment).filter_by(booking_id=booking["id"]).count() == 1


def test_double_payment_with_a_different_key_is_rejected(client):
    """Not a retry - a second, genuinely distinct payment attempt on an
    already-confirmed booking must be rejected, not silently accepted."""
    h = signup_and_login(client, "a@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h, seat_id)

    first = client.post(
        "/payments", json={"booking_id": booking["id"]}, headers={**h, "Idempotency-Key": "pay-a"}
    )
    assert first.status_code == 201

    second = client.post(
        "/payments", json={"booking_id": booking["id"]}, headers={**h, "Idempotency-Key": "pay-b"}
    )
    assert second.status_code == 409


def test_cannot_pay_for_someone_elses_booking(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h1, seat_id)

    r = client.post(
        "/payments", json={"booking_id": booking["id"]}, headers={**h2, "Idempotency-Key": "pay-steal"}
    )
    assert r.status_code == 403


def test_expired_unpaid_booking_is_cancelled_and_frees_the_seat(client, db):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h1, seat_id)

    # Force the booking overdue, as the sweep job would eventually find it.
    row = db.get(Booking, booking["id"])
    row.payment_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    closed = expire_unpaid_bookings(db)
    assert closed == 1
    db.refresh(row)
    assert row.status == "cancelled"

    # Paying for the now-cancelled booking must fail...
    r = client.post(
        "/payments", json={"booking_id": booking["id"]}, headers={**h1, "Idempotency-Key": "too-late"}
    )
    assert r.status_code == 409

    # ...and the seat must be free for someone else.
    seats = client.get(f"/events/{event_id}/seats").json()
    assert next(s for s in seats if s["id"] == seat_id)["available"] is True
    hold_resp = client.post("/holds", json={"seat_id": seat_id}, headers=h2)
    assert hold_resp.status_code == 201


def test_expiry_job_runs_inline_through_the_real_queue(client, db):
    """Proves the RQ wiring, not just the pure function: the job is fetched
    from the real queue machinery and executed, same as a worker would."""
    h = signup_and_login(client, "a@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h, seat_id)

    row = db.get(Booking, booking["id"])
    row.payment_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    conn = redis.Redis(host="localhost", port=6379, db=TEST_REDIS_DB)
    queue = get_queue(connection=conn, is_async=False)  # runs inline, no worker process needed
    job = queue.enqueue(expire_unpaid_bookings_job)

    assert job.result == 1
    db.refresh(row)
    assert row.status == "cancelled"


def test_not_yet_due_booking_is_left_alone(client, db):
    h = signup_and_login(client, "a@example.com")
    event_id = _make_event_with_seats(client)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    booking = book_seat_pending(client, h, seat_id)

    closed = expire_unpaid_bookings(db)
    assert closed == 0
    row = db.get(Booking, booking["id"])
    assert row.status == "pending_payment"
