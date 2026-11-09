"""Fixed-window rate limiting (see app/services/rate_limit.py)."""
import time

import pytest
import redis

from app.config import settings
from app.main import app
from app.redis_client import get_redis
from tests.conftest import _test_redis, make_showtime_with_seats, signup_and_login

@pytest.fixture(autouse=True)
def _start_in_a_fresh_window():
    """The limiter counts in fixed one-minute windows. A test that straddles a
    window boundary would see its counter reset halfway and wrongly miss the 429,
    so when we start within 8 s of the boundary, wait for the next window."""
    window = settings.rate_limit_window_seconds
    into = time.time() % window
    if into > window - 8:
        time.sleep(window - into + 0.1)


LOGIN_BODY = {"email": "a@example.com", "password": "password123"}


def test_login_rate_limit_returns_429_with_retry_after(client):
    client.post("/auth/signup", json=LOGIN_BODY)
    settings.rate_limit_login_per_window = 3

    for _ in range(3):
        assert client.post("/auth/login", json=LOGIN_BODY).status_code == 200

    r = client.post("/auth/login", json=LOGIN_BODY)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_hold_rate_limit_is_per_user_not_global(client):
    """One user tripping the hold limit must not block a different user."""
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seats = make_showtime_with_seats(client, rows=1, seats_per_row=2, price=10)["seats"]

    settings.rate_limit_hold_per_window = 1
    assert client.post("/holds", json={"seat_ids": [seats[0]["id"]]}, headers=h1).status_code == 201
    assert client.post("/holds", json={"seat_ids": [seats[1]["id"]]}, headers=h1).status_code == 429
    # h2 has spent none of their own quota yet.
    assert client.post("/holds", json={"seat_ids": [seats[1]["id"]]}, headers=h2).status_code == 201


def test_hold_rate_limit_returns_429_with_retry_after(client, db):
    h = signup_and_login(client, "a@example.com")
    seats = make_showtime_with_seats(client, rows=1, seats_per_row=2, price=10)["seats"]

    settings.rate_limit_hold_per_window = 1
    assert client.post("/holds", json={"seat_ids": [seats[0]["id"]]}, headers=h).status_code == 201
    r = client.post("/holds", json={"seat_ids": [seats[1]["id"]]}, headers=h)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_rate_limiter_fails_open_when_redis_is_down(client):
    """A broken rate limiter must not take down login - it should log and
    let the request through instead."""
    client.post("/auth/signup", json=LOGIN_BODY)
    settings.rate_limit_login_per_window = 1

    def broken_redis():
        return redis.Redis(host="localhost", port=1, socket_connect_timeout=1)

    app.dependency_overrides[get_redis] = broken_redis
    try:
        # Would 429 on the second call if the limiter were enforcing; instead
        # Redis errors are swallowed and every call gets through.
        assert client.post("/auth/login", json=LOGIN_BODY).status_code == 200
        assert client.post("/auth/login", json=LOGIN_BODY).status_code == 200
    finally:
        app.dependency_overrides[get_redis] = _test_redis


# ---- limits are per IDENTITY (email / phone / user id), not per IP ----------

def test_login_limit_is_per_email_not_per_ip(client):
    """Every request here comes from the same client address."""
    other = {"email": "b@example.com", "password": "password123"}
    client.post("/auth/signup", json=LOGIN_BODY)
    client.post("/auth/signup", json=other)
    settings.rate_limit_login_per_window = 3
    for _ in range(3):
        assert client.post("/auth/login", json=LOGIN_BODY).status_code == 200
    assert client.post("/auth/login", json=LOGIN_BODY).status_code == 429       # account a is throttled...
    assert client.post("/auth/login", json=other).status_code == 200            # ...account b, same IP, is not
    # case does not give a second bucket
    assert client.post("/auth/login", json={**LOGIN_BODY, "email": "A@Example.com"}).status_code == 429


def test_wrong_password_guesses_are_throttled_per_account(client):
    client.post("/auth/signup", json=LOGIN_BODY)
    settings.rate_limit_login_per_window = 3
    wrong = {**LOGIN_BODY, "password": "not-the-password"}
    assert [client.post("/auth/login", json=wrong).status_code for _ in range(4)] == [401, 401, 401, 429]


def test_otp_limits_are_per_phone_number(client):
    settings.rate_limit_login_per_window = 2
    codes = [client.post("/auth/otp/verify", json={"phone": "9111111111", "code": "000000"}).status_code for _ in range(3)]
    assert codes == [401, 401, 429]
    assert client.post("/auth/otp/verify", json={"phone": "9222222222", "code": "000000"}).status_code == 401


def test_gateway_limits_each_user_per_second_and_users_are_independent(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    settings.rate_limit_user_per_second = 2
    codes = [client.get("/me", headers=h1).status_code for _ in range(10)]
    assert 429 in codes and codes[0] == 200
    blocked = client.get("/me", headers=h1)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "1" or 200 in (blocked.status_code,)
    assert client.get("/me", headers=h2).status_code == 200                      # another user is unaffected
    time.sleep(1.1)                                                              # a new second: allowed again
    assert client.get("/me", headers=h1).status_code == 200


def test_gateway_does_not_limit_webhooks_health_or_anonymous_requests(client):
    settings.rate_limit_user_per_second = 1
    for _ in range(5):
        assert client.get("/health").status_code == 200
        assert client.get("/movies").status_code == 200
    # webhooks are authenticated by signature; an unsigned call is a 400, never a 429
    assert all(client.post("/webhooks/payments", content=b"{}").status_code == 400 for _ in range(5))


def test_payments_are_limited_per_user(client):
    h = signup_and_login(client, "a@example.com")
    settings.rate_limit_payment_per_window = 2
    codes = [client.post("/payments", json={"booking_id": 999}, headers={**h, "Idempotency-Key": f"k{i}"}).status_code
             for i in range(3)]
    assert codes == [404, 404, 429]
