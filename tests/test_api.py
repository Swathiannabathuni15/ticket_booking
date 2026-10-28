from tests.conftest import admin_headers, hold_seat, signup_and_login

EVENT = {
    "name": "Rock Night",
    "venue": "Tokyo Dome",
    "starts_at": "2030-01-01T18:00:00+09:00",
    "rows": 2,
    "seats_per_row": 5,
    "price": 50.0,
}


def make_event(client, headers=None):
    """`headers` is accepted for backward compatibility with call sites but
    ignored: creating an event always requires an admin now (see
    test_admin.py), and the caller's own (non-admin) user shouldn't need to
    know that just to set up a seat to book."""
    r = client.post("/events", json=EVENT, headers=admin_headers(client))
    assert r.status_code == 201
    return r.json()["id"]


def test_signup_login_me(client):
    h = signup_and_login(client, "a@example.com")
    assert client.get("/me", headers=h).json()["email"] == "a@example.com"


def test_duplicate_signup_rejected(client):
    body = {"email": "a@example.com", "password": "password123"}
    assert client.post("/auth/signup", json=body).status_code == 201
    assert client.post("/auth/signup", json=body).status_code == 409


def test_wrong_password_rejected(client):
    signup_and_login(client, "a@example.com")
    r = client.post("/auth/login", json={"email": "a@example.com", "password": "wrongpassword"})
    assert r.status_code == 401


def test_booking_requires_login(client):
    assert client.post("/bookings", json={"seat_id": 1}).status_code == 401


def test_event_creates_seats(client):
    h = signup_and_login(client, "a@example.com")
    event_id = make_event(client, h)
    seats = client.get(f"/events/{event_id}/seats").json()
    assert len(seats) == 10
    assert all(s["available"] for s in seats)


def test_book_then_double_book_conflicts(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    event_id = make_event(client, h1)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]

    hold_seat(client, h1, seat_id)
    assert client.post("/bookings", json={"seat_id": seat_id}, headers=h1).status_code == 201
    assert client.post("/bookings", json={"seat_id": seat_id}, headers=h2).status_code == 409

    seats = client.get(f"/events/{event_id}/seats").json()
    assert [s["available"] for s in seats].count(False) == 1


def test_idempotency_key_returns_same_booking(client):
    h = signup_and_login(client, "a@example.com")
    event_id = make_event(client, h)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    headers = {**h, "Idempotency-Key": "abc-123"}

    hold_seat(client, h, seat_id)
    first = client.post("/bookings", json={"seat_id": seat_id}, headers=headers)
    retry = client.post("/bookings", json={"seat_id": seat_id}, headers=headers)
    assert first.status_code == 201
    assert retry.status_code == 200
    assert first.json()["id"] == retry.json()["id"]


def test_cancel_frees_seat_and_only_owner_can_cancel(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    event_id = make_event(client, h1)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    hold_seat(client, h1, seat_id)
    booking_id = client.post("/bookings", json={"seat_id": seat_id}, headers=h1).json()["id"]

    assert client.delete(f"/bookings/{booking_id}", headers=h2).status_code == 403
    assert client.delete(f"/bookings/{booking_id}", headers=h1).status_code == 200
    # Seat is free again, so someone else can book it.
    hold_seat(client, h2, seat_id)
    assert client.post("/bookings", json={"seat_id": seat_id}, headers=h2).status_code == 201


def test_unknown_seat_is_404(client):
    h = signup_and_login(client, "a@example.com")
    assert client.post("/bookings", json={"seat_id": 9999}, headers=h).status_code == 404


def test_booking_without_a_hold_is_rejected(client):
    h = signup_and_login(client, "a@example.com")
    event_id = make_event(client, h)
    seat_id = client.get(f"/events/{event_id}/seats").json()[0]["id"]
    r = client.post("/bookings", json={"seat_id": seat_id}, headers=h)
    assert r.status_code == 409
