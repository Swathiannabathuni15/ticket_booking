"""Best-available picker and the cached seat map."""

from tests.conftest import make_showtime_with_seats, signup_and_login


def test_best_seats_are_adjacent_and_in_one_row(client):
    st = make_showtime_with_seats(client, rows=5, seats_per_row=10)
    r = client.get(f"/showtimes/{st['showtime_id']}/best-seats?count=4")
    assert r.status_code == 200
    labels = [s["label"] for s in r.json()]
    assert len(labels) == 4 and len({lab[0] for lab in labels}) == 1
    nums = sorted(int(lab[1:]) for lab in labels)
    assert nums == list(range(nums[0], nums[0] + 4))


def test_best_seats_skip_taken_seats_and_fail_cleanly(client):
    h = signup_and_login(client, "a@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=6)
    ids = {s["label"]: s["id"] for s in st["seats"]}
    # Hold A3 -> only runs A1-A2 and A4-A6 remain; 4 adjacent is impossible, 3 is fine.
    assert client.post("/holds", json={"seat_ids": [ids["A3"]]}, headers=h).status_code == 201
    other = signup_and_login(client, "b@example.com")
    assert client.get(f"/showtimes/{st['showtime_id']}/best-seats?count=4", headers=other).status_code == 404
    three = client.get(f"/showtimes/{st['showtime_id']}/best-seats?count=3", headers=other).json()
    assert [s["label"] for s in three] == ["A4", "A5", "A6"]
    assert client.get(f"/showtimes/{st['showtime_id']}/best-seats?count=9").status_code == 422


def test_seat_map_reflects_live_holds_despite_cache(client):
    h = signup_and_login(client, "a@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2)
    sid = st["showtime_id"]
    assert [s["status"] for s in client.get(f"/showtimes/{sid}/seats", headers=h).json()] == ["available"] * 2
    client.post("/holds", json={"seat_ids": [st["seats"][0]["id"]]}, headers=h)
    assert client.get(f"/showtimes/{sid}/seats", headers=h).json()[0]["status"] == "held_by_me"
    assert client.get(f"/showtimes/{sid}/seats").json()[0]["status"] == "held"
    assert client.get("/showtimes/99999/seats").status_code == 404
