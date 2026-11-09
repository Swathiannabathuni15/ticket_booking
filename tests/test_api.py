from tests.conftest import hold_seat, make_showtime_with_seats, signup_and_login


def make_showtime(client, rows=2, seats_per_row=5):
    """Returns (showtime_id, seats) - seats as returned by GET
    /showtimes/{id}/seats."""
    result = make_showtime_with_seats(client, rows=rows, seats_per_row=seats_per_row, price=50.0)
    return result["showtime_id"], result["seats"]


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
    assert client.post("/bookings", json={"seat_ids": [1]}).status_code == 401


def test_showtime_creates_seats(client):
    signup_and_login(client, "a@example.com")
    showtime_id, seats = make_showtime(client)
    assert len(seats) == 10
    assert all(s["available"] for s in seats)


def test_book_then_double_book_conflicts(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    showtime_id, seats = make_showtime(client)
    seat_id = seats[0]["id"]

    hold_seat(client, h1, seat_id)
    assert client.post("/bookings", json={"seat_ids": [seat_id]}, headers=h1).status_code == 201
    assert client.post("/bookings", json={"seat_ids": [seat_id]}, headers=h2).status_code == 409

    seats = client.get(f"/showtimes/{showtime_id}/seats").json()
    assert [s["available"] for s in seats].count(False) == 1


def test_idempotency_key_returns_same_booking(client):
    h = signup_and_login(client, "a@example.com")
    showtime_id, seats = make_showtime(client)
    seat_id = seats[0]["id"]
    headers = {**h, "Idempotency-Key": "abc-123"}

    hold_seat(client, h, seat_id)
    first = client.post("/bookings", json={"seat_ids": [seat_id]}, headers=headers)
    retry = client.post("/bookings", json={"seat_ids": [seat_id]}, headers=headers)
    assert first.status_code == 201
    assert retry.status_code == 200
    assert first.json()["id"] == retry.json()["id"]


def test_cancel_frees_seat_and_only_owner_can_cancel(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    showtime_id, seats = make_showtime(client)
    seat_id = seats[0]["id"]
    hold_seat(client, h1, seat_id)
    booking_id = client.post("/bookings", json={"seat_ids": [seat_id]}, headers=h1).json()["id"]

    assert client.delete(f"/bookings/{booking_id}", headers=h2).status_code == 403
    assert client.delete(f"/bookings/{booking_id}", headers=h1).status_code == 200
    # Seat is free again, so someone else can book it.
    hold_seat(client, h2, seat_id)
    assert client.post("/bookings", json={"seat_ids": [seat_id]}, headers=h2).status_code == 201


def test_unknown_seat_is_404(client):
    h = signup_and_login(client, "a@example.com")
    assert client.post("/bookings", json={"seat_ids": [9999]}, headers=h).status_code == 404


def test_booking_without_a_hold_is_rejected(client):
    h = signup_and_login(client, "a@example.com")
    showtime_id, seats = make_showtime(client)
    seat_id = seats[0]["id"]
    r = client.post("/bookings", json={"seat_ids": [seat_id]}, headers=h)
    assert r.status_code == 409
