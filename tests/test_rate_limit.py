"""Fixed-window rate limiting (see app/services/rate_limit.py)."""
import redis

from app.config import settings
from app.main import app
from app.redis_client import get_redis
from tests.conftest import _test_redis, admin_headers, signup_and_login

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
    event = client.post(
        "/events",
        json={
            "name": "E", "venue": "V", "starts_at": "2030-01-01T18:00:00+09:00",
            "rows": 1, "seats_per_row": 2, "price": 10,
        },
        headers=admin_headers(client),
    ).json()
    seats = client.get(f"/events/{event['id']}/seats").json()

    settings.rate_limit_hold_per_window = 1
    assert client.post("/holds", json={"seat_id": seats[0]["id"]}, headers=h1).status_code == 201
    assert client.post("/holds", json={"seat_id": seats[1]["id"]}, headers=h1).status_code == 429
    # h2 has spent none of their own quota yet.
    assert client.post("/holds", json={"seat_id": seats[1]["id"]}, headers=h2).status_code == 201


def test_hold_rate_limit_returns_429_with_retry_after(client, db):
    h = signup_and_login(client, "a@example.com")
    event = client.post(
        "/events",
        json={
            "name": "E", "venue": "V", "starts_at": "2030-01-01T18:00:00+09:00",
            "rows": 1, "seats_per_row": 2, "price": 10,
        },
        headers=admin_headers(client),
    ).json()
    seats = client.get(f"/events/{event['id']}/seats").json()

    settings.rate_limit_hold_per_window = 1
    assert client.post("/holds", json={"seat_id": seats[0]["id"]}, headers=h).status_code == 201
    r = client.post("/holds", json={"seat_id": seats[1]["id"]}, headers=h)
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
