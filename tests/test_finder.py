"""The seat finder: filters on one hall, and ranking the best seats across a movie's shows."""
from app.routers.showtimes import best_block, pick_best_seats
from app.schemas import SeatOut
from tests.conftest import admin_headers, signup_and_login


def _hall(rows="ABCD", cols=10, price_of=lambda row: 100.0):
    """A is the BACK row, the last letter is nearest the screen."""
    out, sid = [], 1
    for row in rows:
        for n in range(1, cols + 1):
            out.append(SeatOut(id=sid, label=f"{row}{n}", seat_type="std", price=price_of(row), status="available", available=True))
            sid += 1
    return out


def test_plain_best_block_is_centred_and_a_little_back():
    b = best_block(_hall(), 2)
    assert len(b.seats) == 2 and abs(b.off_centre) <= 0.5
    assert b.seats[0].label[0] in "BC"                         # not the extreme front or the very back
    assert pick_best_seats(_hall(), 2) == b.seats              # the old helper still works


def test_centre_only_keeps_to_the_middle_of_the_row():
    hall = _hall(rows="A")
    for s in hall:                                             # make the middle unavailable
        if 3 <= int(s.label[1:]) <= 8:
            s.status, s.available = "booked", False
    assert best_block(hall, 2) is not None                     # edge seats exist...
    assert best_block(hall, 2, centre_only=True) is None       # ...but they are not "centre"


def test_not_too_close_skips_the_front_rows():
    b = best_block(_hall(), 2, min_rows_from_screen=3)
    assert b.rows_from_screen >= 3 and b.seats[0].label.startswith("A")          # only the back row is far enough
    assert best_block(_hall(), 2, min_rows_from_screen=4) is None                # no row that far


def test_price_cap_applies_to_every_seat_in_the_group():
    hall = _hall(price_of=lambda row: 300.0 if row in "AB" else 150.0)
    b = best_block(hall, 3, max_price=200)
    assert b is not None and all(s.price <= 200 for s in b.seats) and b.seats[0].label[0] in "CD"
    assert best_block(hall, 3, max_price=100) is None


def test_seats_must_be_together_across_gaps_and_taken_seats():
    hall = _hall(rows="A", cols=6)
    hall[2].status = "held"                                    # A3 gone: runs are A1-A2 and A4-A6
    assert best_block(hall, 4) is None
    assert [s.label for s in best_block(hall, 3).seats] == ["A4", "A5", "A6"]


# ---- across shows ----------------------------------------------------------

def _movie_with_shows(client, times, city="Testville", rows=4, cols=10, price=100):
    adm = admin_headers(client, "finder-admin@example.com")
    m = client.post("/movies", json={"title": "Finder Movie", "duration_minutes": 100}, headers=adm).json()
    th = client.post("/theatres", json={"name": f"T-{city}", "city": city}, headers=adm).json()
    layout = [{"row": "ABCDEFGH"[i], "seats": ["std"] * cols} for i in range(rows)]
    sc = client.post("/screens", json={"theatre_id": th["id"], "name": "S1", "rows": layout}, headers=adm).json()
    ids = []
    for t in times:
        ids.append(client.post("/showtimes", json={"movie_id": m["id"], "screen_id": sc["id"], "starts_at": t,
                                                  "prices": {"std": price}}, headers=adm).json()["id"])
    return m["id"], ids


TIMES = ["2030-06-01T10:00:00+05:30", "2030-06-01T14:00:00+05:30", "2030-06-01T18:30:00+05:30", "2030-06-01T22:30:00+05:30"]


def test_returns_the_best_seats_per_show_sorted_best_first(client):
    movie, shows = _movie_with_shows(client, TIMES)
    h = signup_and_login(client, "taker@example.com")
    # someone is holding the best (centre, middle-back) seats in the 10:00 show, so it must rank lower
    seats = client.get(f"/showtimes/{shows[0]}/seats").json()
    centre_ids = [s["id"] for s in seats if s["label"][0] in "BC" and s["label"][1:] in ("4", "5", "6", "7")]
    assert client.post("/holds", json={"seat_ids": centre_ids}, headers=h).status_code == 201
    r = client.get(f"/movies/{movie}/best-seats?count=2")
    assert r.status_code == 200 and len(r.json()) == 4
    scores = [x["score"] for x in r.json()]
    assert scores == sorted(scores)
    assert r.json()[-1]["showtime_id"] == shows[0]            # the disturbed show is last
    first = r.json()[0]
    assert len(first["seat_ids"]) == 2 and first["theatre_name"] == "T-Testville" and first["total_price"] == 200.0


def test_time_of_day_filter_uses_the_cinemas_clock(client):
    movie, shows = _movie_with_shows(client, TIMES)
    by = lambda tod: {x["showtime_id"] for x in client.get(f"/movies/{movie}/best-seats?count=2&time_of_day={tod}").json()}
    assert by("morning") == {shows[0]} and by("afternoon") == {shows[1]}
    assert by("evening") == {shows[2]} and by("night") == {shows[3]}
    assert client.get(f"/movies/{movie}/best-seats?time_of_day=lunch").status_code == 422


def test_filters_narrow_the_results(client):
    movie, shows = _movie_with_shows(client, TIMES[:1], rows=8, price=100)
    base = client.get(f"/movies/{movie}/best-seats?count=2").json()[0]
    far = client.get(f"/movies/{movie}/best-seats?count=2&not_too_close=true").json()[0]
    assert far["rows_from_screen"] >= 2 and far["rows_from_screen"] >= base["rows_from_screen"] - 6
    assert client.get(f"/movies/{movie}/best-seats?count=2&max_price=50").json() == []          # too expensive for the cap
    assert len(client.get(f"/movies/{movie}/best-seats?count=2&max_price=100&centre_only=true").json()) == 1
    assert client.get(f"/movies/{movie}/best-seats?count=10").status_code == 422               # at most 8 seats


def test_city_filter_and_unknown_movie(client):
    movie, _ = _movie_with_shows(client, TIMES[:1], city="Pune")
    assert len(client.get(f"/movies/{movie}/best-seats?count=2&city=pune").json()) == 1        # not case sensitive
    assert client.get(f"/movies/{movie}/best-seats?count=2&city=Mumbai").json() == []
    assert client.get("/movies/99999/best-seats").status_code == 404


def test_past_shows_are_never_suggested(client):
    movie, _ = _movie_with_shows(client, ["2020-01-01T18:00:00+05:30"])
    assert client.get(f"/movies/{movie}/best-seats?count=2").json() == []
