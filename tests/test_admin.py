"""Admin role: only admins can create events (see app/deps.py require_admin)."""
from sqlalchemy import select

from app.models import User
from tests.conftest import admin_headers, signup_and_login

EVENT = {
    "name": "E", "venue": "V", "starts_at": "2030-01-01T18:00:00+09:00",
    "rows": 1, "seats_per_row": 1, "price": 10.0,
}


def test_non_admin_cannot_create_event(client):
    h = signup_and_login(client, "a@example.com")
    r = client.post("/events", json=EVENT, headers=h)
    assert r.status_code == 403


def test_admin_can_create_event(client):
    h = admin_headers(client)
    r = client.post("/events", json=EVENT, headers=h)
    assert r.status_code == 201


def test_logged_out_request_gets_401_not_403(client):
    """Missing auth and insufficient privilege are different failures -
    401 means "who are you", 403 means "I know who you are and you can't"."""
    r = client.post("/events", json=EVENT)
    assert r.status_code == 401


def test_me_reports_admin_status(client, db):
    h = signup_and_login(client, "a@example.com")
    assert client.get("/me", headers=h).json()["is_admin"] is False

    user = db.scalar(select(User).where(User.email == "a@example.com"))
    user.is_admin = True
    db.commit()

    assert client.get("/me", headers=h).json()["is_admin"] is True
