"""Mobile OTP sign-in, movie metadata, and city filtering."""

from datetime import UTC

from app.config import settings
from tests.conftest import admin_headers, signup_and_login


def test_otp_signin_creates_user_and_logs_in(client):
    r = client.post("/auth/otp/request", json={"phone": "9876543210"})
    assert r.status_code == 200 and len(r.json()["dev_otp"]) == 6
    code = r.json()["dev_otp"]
    tok = client.post("/auth/otp/verify", json={"phone": "9876543210", "code": code})
    assert tok.status_code == 200
    me = client.get("/me", headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).json()
    assert me["phone"] == "+9876543210" and me["email"] is None
    # single use
    assert client.post("/auth/otp/verify", json={"phone": "9876543210", "code": code}).status_code == 401
    # a second sign-in is the SAME user
    code2 = client.post("/auth/otp/request", json={"phone": "+9876543210"}).json()["dev_otp"]
    tok2 = client.post("/auth/otp/verify", json={"phone": "9876543210", "code": code2}).json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {tok2}"}).json()["id"] == me["id"]


def test_otp_wrong_code_and_lockout(client):
    code = client.post("/auth/otp/request", json={"phone": "9000000001"}).json()["dev_otp"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.otp_max_attempts):
        assert client.post("/auth/otp/verify", json={"phone": "9000000001", "code": wrong}).status_code == 401
    # attempts exhausted: even the RIGHT code no longer works
    assert client.post("/auth/otp/verify", json={"phone": "9000000001", "code": code}).status_code == 401


def test_otp_validation_and_request_rate_limit(client):
    assert client.post("/auth/otp/request", json={"phone": "abc"}).status_code == 422
    assert client.post("/auth/otp/verify", json={"phone": "9000000002", "code": "12"}).status_code == 422
    codes = [client.post("/auth/otp/request", json={"phone": "9000000003"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_otp_without_a_provider_and_demo_mode_off_is_refused(client):
    """No SMS/email provider and demo mode off: say so, instead of generating a code nobody can read."""
    settings.otp_dev_mode = False
    try:
        r = client.post("/auth/otp/request", json={"phone": "9000000004"})
        assert r.status_code == 503
    finally:
        settings.otp_dev_mode = True


def _showtime(client, adm, city, title, **movie_extra):
    movie = client.post("/movies", json={"title": title, "duration_minutes": 100, **movie_extra}, headers=adm).json()
    theatre = client.post("/theatres", json={"name": f"T-{city}", "city": city}, headers=adm).json()
    screen = client.post(
        "/screens", json={"theatre_id": theatre["id"], "name": "S", "rows": [{"row": "A", "seats": ["x"]}]}, headers=adm
    ).json()
    st = client.post(
        "/showtimes",
        json={
            "movie_id": movie["id"],
            "screen_id": screen["id"],
            "starts_at": "2030-01-01T18:00:00",
            "prices": {"x": 5},
        },
        headers=adm,
    )
    return movie, st.json()


def test_movie_metadata_and_city_filtering(client):
    adm = admin_headers(client)
    m1, st1 = _showtime(
        client,
        adm,
        "Hyderabad",
        "Ramayana",
        genre="Action/Myth",
        language="Hindi",
        rating=9.3,
        poster_url="https://example.com/p.jpg",
    )
    _showtime(client, adm, "Mumbai", "Scene")
    assert m1["genre"] == "Action/Myth" and m1["language"] == "Hindi" and m1["rating"] == 9.3
    assert st1["city"] == "Hyderabad" and st1["theatre_name"] == "T-Hyderabad"
    assert sorted(client.get("/cities").json()) == ["Hyderabad", "Mumbai"]
    assert [m["title"] for m in client.get("/movies?city=hyderabad").json()] == ["Ramayana"]  # case-insensitive
    assert len(client.get("/movies").json()) == 2
    assert [s["city"] for s in client.get("/showtimes?city=Mumbai").json()] == ["Mumbai"]
    assert client.get("/movies?city=Nowhere").json() == []


def test_movie_poster_must_be_http_url(client):
    adm = admin_headers(client)
    r = client.post(
        "/movies", json={"title": "X", "duration_minutes": 90, "poster_url": "javascript:alert(1)"}, headers=adm
    )
    assert r.status_code == 422


def test_get_single_showtime(client):
    adm = admin_headers(client)
    _, st = _showtime(client, adm, "Pune", "Solo")
    got = client.get(f"/showtimes/{st['id']}").json()
    assert got["city"] == "Pune" and got["movie_title"] == "Solo"
    assert client.get("/showtimes/99999").status_code == 404


def test_session_is_long_and_can_be_refreshed(client):
    from datetime import datetime

    import jwt

    h = signup_and_login(client, "stay@example.com")
    r = client.post("/auth/refresh", headers=h)
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    exp = jwt.decode(token, options={"verify_signature": False})["exp"]
    days = (datetime.fromtimestamp(exp, UTC) - datetime.now(UTC)).days
    assert 28 <= days <= 30  # about a month
    assert client.post("/auth/refresh").status_code == 401  # no token, no refresh
    assert client.post("/auth/refresh", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_admin_can_update_a_movie_poster_but_others_cannot(client):
    adm = admin_headers(client)
    user = signup_and_login(client, "u@example.com")
    m = client.post("/movies", json={"title": "Poster Test", "duration_minutes": 100}, headers=adm).json()
    url = "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert client.patch(f"/movies/{m['id']}", json={"poster_url": url}, headers=user).status_code == 403
    assert client.patch(f"/movies/{m['id']}", json={"poster_url": url}).status_code == 401
    r = client.patch(f"/movies/{m['id']}", json={"poster_url": url, "rating": 8.1}, headers=adm)
    assert r.status_code == 200 and r.json()["poster_url"] == url and r.json()["title"] == "Poster Test"
    assert (
        client.patch(f"/movies/{m['id']}", json={"poster_url": "javascript:alert(1)"}, headers=adm).status_code == 422
    )
    assert client.patch("/movies/99999", json={"rating": 5}, headers=adm).status_code == 404


def test_booking_carries_the_theatre_for_directions(client):
    from tests.conftest import book_seat_pending, make_showtime_with_seats

    h = signup_and_login(client, "d@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2)
    b = book_seat_pending(client, h, st["seats"][0]["id"])
    assert b["theatre_name"] == "Test Theatre" and b["city"] == "Testville"
