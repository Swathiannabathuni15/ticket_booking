"""Seat holds: the Redis-backed reservation that gates booking."""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import redis

from app.main import app
from app.models import Event, Seat
from app.redis_client import get_redis
from tests.conftest import _test_redis, admin_headers, hold_seat, signup_and_login

EVENT = {
    "name": "Rock Night",
    "venue": "Tokyo Dome",
    "starts_at": "2030-01-01T18:00:00+09:00",
    "rows": 1,
    "seats_per_row": 1,
    "price": 50.0,
}


def make_event_with_one_seat(client, headers=None) -> int:
    event_id = client.post("/events", json=EVENT, headers=admin_headers(client)).json()["id"]
    return client.get(f"/events/{event_id}/seats").json()[0]["id"]


def test_second_user_cannot_hold_an_already_held_seat(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seat_id = make_event_with_one_seat(client, h1)

    hold_seat(client, h1, seat_id)
    r = client.post("/holds", json={"seat_id": seat_id}, headers=h2)
    assert r.status_code == 409


def test_holding_your_own_seat_again_refreshes_it_instead_of_failing(client):
    h = signup_and_login(client, "a@example.com")
    seat_id = make_event_with_one_seat(client, h)

    hold_seat(client, h, seat_id)
    r = client.post("/holds", json={"seat_id": seat_id}, headers=h)
    assert r.status_code == 201


def test_booking_without_a_valid_hold_is_rejected(client, db):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seat_id = make_event_with_one_seat(client, h1)

    hold_seat(client, h1, seat_id)
    # h2 has no hold on this seat, so booking must be rejected even though the seat is free.
    r = client.post("/bookings", json={"seat_id": seat_id}, headers=h2)
    assert r.status_code == 409


def test_releasing_a_hold_lets_someone_else_hold_it(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seat_id = make_event_with_one_seat(client, h1)

    hold_seat(client, h1, seat_id)
    assert client.delete(f"/holds/{seat_id}", headers=h1).status_code == 204
    hold_seat(client, h2, seat_id)  # would 409 if the release didn't work


def test_a_successful_booking_releases_its_own_hold(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seat_id = make_event_with_one_seat(client, h1)

    hold_seat(client, h1, seat_id)
    booked = client.post("/bookings", json={"seat_id": seat_id}, headers=h1)
    assert booked.status_code == 201
    # h1's hold is gone now, but the seat is booked, so h2 still can't hold it.
    assert client.post("/holds", json={"seat_id": seat_id}, headers=h2).status_code == 409


def test_a_retried_booking_succeeds_after_the_hold_is_used_up(client):
    """The idempotency key must still work even though the first call already
    consumed (released) the hold - a retry shouldn't need to re-hold."""
    h = signup_and_login(client, "a@example.com")
    seat_id = make_event_with_one_seat(client, h)
    headers = {**h, "Idempotency-Key": "retry-1"}

    hold_seat(client, h, seat_id)
    first = client.post("/bookings", json={"seat_id": seat_id}, headers=headers)
    assert first.status_code == 201

    retry = client.post("/bookings", json={"seat_id": seat_id}, headers=headers)
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]


def test_hold_expires_on_its_own(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seat_id = make_event_with_one_seat(client, h1)

    r = _test_redis()
    r.set(f"seat_hold:{seat_id}", "999:fake-token", ex=1)

    time.sleep(1.2)
    hold_seat(client, h2, seat_id)  # succeeds now that the old hold has expired


def test_a_late_release_does_not_delete_someone_elses_hold(client):
    """h1 holds, their hold expires, h2 takes the seat. h1's (now stale)
    release call arrives late and must be a harmless no-op, not a delete of
    h2's live hold."""
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seat_id = make_event_with_one_seat(client, h1)

    r = _test_redis()
    r.set(f"seat_hold:{seat_id}", "999:h1-fake-token", ex=1)  # stand in for h1's hold, about to expire
    time.sleep(1.2)

    hold_seat(client, h2, seat_id)  # h2 holds the now-free seat

    # h1's release, arriving late, must not touch h2's hold.
    assert client.delete(f"/holds/{seat_id}", headers=h1).status_code == 204
    assert client.post("/bookings", json={"seat_id": seat_id}, headers=h2).status_code == 201


def test_100_users_race_for_one_hold_exactly_one_wins(db):
    event = Event(name="Last Seat", venue="Test", starts_at=datetime.now(timezone.utc))
    db.add(event)
    db.flush()
    seat = Seat(event_id=event.id, label="A1", price=10)
    db.add(seat)
    db.commit()
    seat_id = seat.id

    from app.services import hold_service as holds

    def try_hold(user_id: int) -> str:
        r = redis.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        try:
            holds.acquire_hold(r, seat_id, user_id)
            return "won"
        except holds.SeatHeldByOther:
            return "lost"
        finally:
            r.close()

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(try_hold, range(100)))

    assert results.count("won") == 1
    assert results.count("lost") == 99


def test_redis_down_holds_fail_closed_but_login_still_works(client, db):
    """If Redis is unreachable: holds (and therefore booking) must fail with
    503, but login/signup - which only consult Redis for rate limiting, and
    fail OPEN there - must keep working."""
    h = signup_and_login(client, "a@example.com")
    event_id = client.post("/events", json=EVENT, headers=admin_headers(client)).json()["id"]
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]

    def broken_redis():
        # Port 1 is not listening, so any call raises a ConnectionError fast
        # thanks to the short socket_connect_timeout in app.redis_client.
        return redis.Redis(host="localhost", port=1, socket_connect_timeout=1)

    app.dependency_overrides[get_redis] = broken_redis
    try:
        hold_resp = client.post("/holds", json={"seat_id": seat_id}, headers=h)
        assert hold_resp.status_code == 503

        book_resp = client.post("/bookings", json={"seat_id": seat_id}, headers=h)
        assert book_resp.status_code == 503

        login_resp = client.post(
            "/auth/login", json={"email": "a@example.com", "password": "password123"}
        )
        assert login_resp.status_code == 200
    finally:
        app.dependency_overrides[get_redis] = _test_redis
