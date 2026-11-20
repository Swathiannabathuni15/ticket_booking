"""Logout that really ends a session, bounded refresh, and the test-card switch."""

import importlib.util
import time
from pathlib import Path

import jwt
import pytest

from app.config import settings
from app.main import app
from app.redis_client import get_redis
from app.security import create_access_token, decode_token
from tests.conftest import _test_redis, book_seat_pending, make_showtime_with_seats, signup_and_login

signup = signup_and_login


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, email="a@example.com"):
    client.post("/auth/signup", json={"email": email, "password": "password123"})
    return client.post("/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]


def test_every_token_has_its_own_id_and_remembers_when_the_login_began():
    a, b = create_access_token(1), create_access_token(1)
    ca, cb = decode_token(a), decode_token(b)
    assert ca["jti"] != cb["jti"] and abs(ca["sst"] - time.time()) < 5


def test_logout_stops_the_token_working_everywhere(client):
    token = _login(client)
    assert client.get("/me", headers=_bearer(token)).status_code == 200
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 204
    assert client.get("/me", headers=_bearer(token)).status_code == 401  # a COPY of the token is dead too
    assert client.post("/auth/refresh", headers=_bearer(token)).status_code == 401
    # other sign-ins of the same person are untouched
    other = client.post("/auth/login", json={"email": "a@example.com", "password": "password123"}).json()[
        "access_token"
    ]
    assert client.get("/me", headers=_bearer(other)).status_code == 200


def test_logout_always_succeeds_and_is_repeatable(client):
    token = _login(client)
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 204
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 204  # already dead: still fine
    assert client.post("/auth/logout").status_code == 204  # no token at all
    assert client.post("/auth/logout", headers=_bearer("garbage")).status_code == 204


def test_a_logged_out_token_is_also_ignored_by_public_pages(client):
    """The seat map shows 'held by you' only for a valid login."""
    token = _login(client)
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2)
    sid, seat = st["showtime_id"], st["seats"][0]["id"]
    client.post("/holds", json={"seat_ids": [seat]}, headers=_bearer(token))
    assert client.get(f"/showtimes/{sid}/seats", headers=_bearer(token)).json()[0]["status"] == "held_by_me"
    client.post("/auth/logout", headers=_bearer(token))
    assert (
        client.get(f"/showtimes/{sid}/seats", headers=_bearer(token)).json()[0]["status"] == "held"
    )  # now just "someone's"


def test_refresh_keeps_the_original_start_time(client):
    token = _login(client)
    started = decode_token(token)["sst"]
    new = client.post("/auth/refresh", headers=_bearer(token)).json()["access_token"]
    assert decode_token(new)["sst"] == started and decode_token(new)["jti"] != decode_token(token)["jti"]


def test_a_session_cannot_be_renewed_forever(client):
    _login(client)
    uid = client.get("/me", headers=_bearer(_login(client))).json()["id"]
    old_start = int(time.time()) - (settings.max_session_days + 1) * 86400
    stale = create_access_token(uid, session_start=old_start)  # valid token, but the login is too old
    assert client.get("/me", headers=_bearer(stale)).status_code == 200  # it still WORKS until it expires...
    r = client.post("/auth/refresh", headers=_bearer(stale))
    assert r.status_code == 401 and "too old" in r.json()["detail"]  # ...but cannot be extended


def test_tokens_made_before_this_feature_still_work(client):
    """No jti / sst claim: cannot be revoked, but must not break existing logins."""
    uid = client.get("/me", headers=_bearer(_login(client))).json()["id"]
    old = jwt.encode(
        {"sub": str(uid), "exp": int(time.time()) + 600}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    assert client.get("/me", headers=_bearer(old)).status_code == 200
    assert client.post("/auth/refresh", headers=_bearer(old)).status_code == 200


def test_when_redis_is_down_logins_still_work_and_logout_does_not_crash(client):
    token = _login(client)
    app.dependency_overrides[get_redis] = lambda: __import__("redis").Redis(
        host="localhost", port=1, socket_connect_timeout=1
    )
    try:
        assert client.get("/me", headers=_bearer(token)).status_code == 200  # fail open: the site stays up
        assert client.post("/auth/logout", headers=_bearer(token)).status_code == 204
    finally:
        app.dependency_overrides[get_redis] = _test_redis


# ---- the test-card switch --------------------------------------------------


def test_config_tells_the_website_whether_test_cards_exist(client):
    settings.gateway_allow_test_methods = True
    assert client.get("/config").json() == {"test_cards": True, "payment_provider": "simulated"}
    settings.gateway_allow_test_methods = False
    try:
        assert client.get("/config").json() == {"test_cards": False, "payment_provider": "simulated"}
    finally:
        settings.gateway_allow_test_methods = True


def test_with_test_cards_off_the_browser_cannot_choose_which_card_works(client):
    """The browser asks for a declined card; the server must ignore that and charge normally."""
    h = signup(client, "p@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2)
    b = book_seat_pending(client, h, st["seats"][0]["id"])
    settings.gateway_allow_test_methods = False
    try:
        r = client.post(
            "/payments",
            json={"booking_id": b["id"], "payment_method": "tok_decline"},
            headers={**h, "Idempotency-Key": "key-no-test-cards"},
        )
        assert r.status_code == 201 and r.json()["status"] == "confirmed"
    finally:
        settings.gateway_allow_test_methods = True


# ---- the seed-script password guard -----------------------------------------


def _safety():
    spec = importlib.util.spec_from_file_location(
        "_safety", Path(__file__).resolve().parents[1] / "scripts" / "_safety.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seed_scripts_refuse_the_public_password_on_a_real_server():
    s = _safety()
    with pytest.raises(SystemExit) as e:
        s.require_real_admin_password("https://tickets.example.com", "demo-password-1")
    assert "ADMIN_PASSWORD" in str(e.value)
    s.require_real_admin_password("https://tickets.example.com", "a-strong-password-9")  # a real password is fine
    for local in ("http://localhost:8000", "http://127.0.0.1:8000"):
        s.require_real_admin_password(local, "demo-password-1")  # your own laptop is fine
