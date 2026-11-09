"""Admin role: only admins can create catalogue data (see app/deps.py require_admin)."""
from sqlalchemy import select

from app.models import User
from tests.conftest import admin_headers, signup_and_login

MOVIE = {"title": "Test Movie", "duration_minutes": 120}


def test_non_admin_cannot_create_movie(client):
    h = signup_and_login(client, "a@example.com")
    r = client.post("/movies", json=MOVIE, headers=h)
    assert r.status_code == 403


def test_admin_can_create_movie(client):
    h = admin_headers(client)
    r = client.post("/movies", json=MOVIE, headers=h)
    assert r.status_code == 201


def test_logged_out_request_gets_401_not_403(client):
    """Missing auth and insufficient privilege are different failures -
    401 means "who are you", 403 means "I know who you are and you can't"."""
    r = client.post("/movies", json=MOVIE)
    assert r.status_code == 401


def test_me_reports_admin_status(client, db):
    h = signup_and_login(client, "a@example.com")
    assert client.get("/me", headers=h).json()["is_admin"] is False

    user = db.scalar(select(User).where(User.email == "a@example.com"))
    user.is_admin = True
    db.commit()

    assert client.get("/me", headers=h).json()["is_admin"] is True


def test_admin_bookings_list_and_cors(client):
    from tests.conftest import admin_headers, signup_and_login
    user = signup_and_login(client, "u@example.com")
    assert client.get("/admin/bookings").status_code == 401
    assert client.get("/admin/bookings", headers=user).status_code == 403
    admin = admin_headers(client)
    assert client.get("/admin/bookings", headers=admin).json() == []
    r = client.options(
        "/bookings/me",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "authorization,idempotency-key"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_allows_any_localhost_port_but_not_other_sites(client):
    def origin_header(origin):
        r = client.options("/bookings/me", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        return r.headers.get("access-control-allow-origin")
    assert origin_header("http://localhost:5174") == "http://localhost:5174"
    assert origin_header("http://127.0.0.1:5199") == "http://127.0.0.1:5199"
    assert origin_header("https://evil.example.com") is None
    assert origin_header("http://localhost.evil.com") is None
