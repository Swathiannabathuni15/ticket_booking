"""Smart seat picking: viewing quality, price, groups, and the awkward cases."""
import pytest

from app.schemas import SeatOut
from app.services import seat_scoring as ss
from tests.conftest import make_showtime_with_seats


def hall(rows="ABCDEFGH", cols=12, price=lambda row, n: 100.0, kind=lambda row, n: "std", taken=(), aisle_at=()):
    """Row A is the back. `taken` = labels not free. `aisle_at` = column numbers that are aisles (no seat)."""
    out, sid = [], 1
    for row in rows:
        for n in range(1, cols + 1):
            if n in aisle_at:
                continue
            label = f"{row}{n}"
            st = "booked" if label in taken else "available"
            out.append(SeatOut(id=sid, label=label, seat_type=kind(row, n), price=price(row, n), status=st, available=st == "available"))
            sid += 1
    return out


def labels(c):
    return [s.label for s in c.seats]


# ---- how well will you see? ---------------------------------------------------------------

def test_the_front_rows_are_poor_and_the_middle_rows_are_good():
    h = ss.build_hall(hall(rows="ABCDEFGHIJKLMNOPQ", cols=18))
    vis = {row: ss.seat_metrics(h, row, 9).visibility for row in h.rows}
    assert vis["Q"] < 40 < 90 < vis["F"]                         # nearest the screen vs a good row
    assert vis["P"] < vis["N"] < vis["L"]                        # gets worse the closer you sit
    m_front = ss.seat_metrics(h, "Q", 9)
    assert m_front.neck > 50 and m_front.fov > 110               # tilting up hard, screen swamps you


def test_sitting_to_the_side_costs_visibility():
    h = ss.build_hall(hall(rows="ABCDEFGHIJKLMNOPQ", cols=18))
    centre, edge = ss.seat_metrics(h, "F", 9), ss.seat_metrics(h, "F", 1)
    assert edge.off_axis > centre.off_axis + 5 and edge.visibility < centre.visibility


def test_best_view_is_centred_and_not_jammed_against_the_screen():
    opts, msg = ss.rank_options(hall(rows="ABCDEFGHIJKLMNOPQ", cols=18), 2)
    top = opts[0].cand
    assert "Best view" in opts[0].labels and top.visibility >= 95 and abs(top.off_centre) <= 0.5
    assert 6 <= top.rows_from_screen <= 14 and msg is None


# ---- groups --------------------------------------------------------------------------------

def test_a_group_is_always_side_by_side_in_one_row_and_one_price_tier():
    seats = hall(rows="A", cols=6, kind=lambda r, n: "gold" if n <= 3 else "silver", price=lambda r, n: 200.0 if n <= 3 else 100.0)
    cs = ss.candidates(seats, 2)
    assert all(len({s.seat_type for s in c.seats}) == 1 for c in cs)         # no gold+silver booking
    assert [labels(c) for c in cs if "A3" in labels(c) and "A4" in labels(c)] == []
    assert ss.candidates(seats, 4) == []                                     # 3 gold + 3 silver: no 4 of one tier


def test_taken_seats_break_a_group_and_an_aisle_does_not_count_as_together_by_default():
    seats = hall(rows="A", cols=8, taken={"A4"}, aisle_at={7})
    assert ss.candidates(seats, 4) == []                                     # A1-A3 | A5-A6 | (aisle) A8
    crossed = ss.candidates(seats, 3, allow_aisle_cross=True)                # A5,A6,(aisle),A8
    assert [labels(c) for c in crossed if c.across_aisle] == [["A5", "A6", "A8"]]


def test_when_nothing_fits_together_an_aisle_split_is_offered_with_a_note():
    seats = hall(rows="A", cols=8, taken={"A1", "A2", "A3", "A4"}, aisle_at={7})     # free: A5 A6 (aisle) A8
    opts, msg = ss.rank_options(seats, 3)
    assert opts and opts[0].cand.across_aisle and labels(opts[0].cand) == ["A5", "A6", "A8"]
    assert "aisle" in msg and any("walkway" in w for w in opts[0].warnings)


def test_no_group_possible_or_sold_out_say_so_plainly():
    opts, msg = ss.rank_options(hall(rows="A", cols=4, taken={"A2"}), 3)
    assert opts == [] and "No 3 seats together" in msg
    opts, msg = ss.rank_options(hall(rows="A", cols=3, taken={"A1", "A2", "A3"}), 1)
    assert opts == [] and "sold out" in msg


# ---- real-life details ---------------------------------------------------------------------

def test_a_group_that_strands_a_single_seat_is_ranked_below_one_that_does_not():
    # free: A1 .. A5, A6 taken. Booking A1-A2 leaves A3-A5 (fine); booking A2-A3 strands A1.
    seats = hall(rows="A", cols=6, taken={"A6"})
    by = {tuple(labels(c)): c for c in ss.candidates(seats, 2)}
    assert by[("A2", "A3")].orphans == 1 and by[("A1", "A2")].orphans == 0
    assert by[("A1", "A2")].utility() > by[("A2", "A3")].utility() - 100        # sanity
    assert by[("A2", "A3")].utility() == by[("A2", "A3")].visibility - ss.ORPHAN_PENALTY - (
        0.15 * abs(by[("A2", "A3")].fov - ss.SWEET_SPOT_FOV) + 0.2 * by[("A2", "A3")].off_axis)
    opts, _ = ss.rank_options(seats, 2)
    assert "A1" in labels(opts[0].cand) or "A4" in labels(opts[0].cand)         # a choice that strands nobody
    assert opts[0].cand.orphans == 0


def test_a_warning_appears_when_a_lone_seat_is_unavoidable():
    seats = hall(rows="A", cols=4, taken={"A4"})                # A1 A2 A3 free: any pair strands one seat
    opts, _ = ss.rank_options(seats, 2)
    assert any("single empty seat" in w for w in opts[0].warnings)


def test_asking_for_an_aisle_seat_prefers_one():
    seats = hall(rows="ABCDEFGHIJKLMNOPQ", cols=12, aisle_at={5, 9})             # aisles after columns 4 and 8
    plain = ss.rank_options(seats, 2)[0][0].cand
    aisle = ss.rank_options(seats, 2, prefer_aisle=True)[0][0].cand
    assert aisle.aisle and aisle.visibility >= plain.visibility - 8              # a small price in view, never a poor seat
    assert any("Aisle seat" in r for r in ss.rank_options(seats, 2, prefer_aisle=True)[0][0].reasons)


# ---- price ---------------------------------------------------------------------------------

TIER = dict(rows="ABCDEFGHIJKLMNOPQ", cols=12,
            kind=lambda r, n: "platinum" if r < "D" else "gold" if r < "M" else "silver",
            price=lambda r, n: 350.0 if r < "D" else 250.0 if r < "M" else 190.0)


def test_best_value_does_not_pay_for_a_tier_that_does_not_improve_the_view():
    seats = hall(**TIER)
    opts, _ = ss.rank_options(seats, 2)
    assert all(o.cand.seat_type != "platinum" for o in opts)                 # platinum costs 40% more for no better view
    view = next(o for o in opts if "Best view" in o.labels)
    value = next(o for o in opts if "Best value" in o.labels)
    assert value.cand.total <= view.cand.total and value.cand.visibility >= 85       # cheaper or equal, and still a great view


def test_cheapest_must_still_be_decent_to_see_from():
    seats = hall(**TIER)
    cheapest = [o for o in ss.rank_options(seats, 2)[0] if "Cheapest" in o.labels][0]
    assert cheapest.cand.visibility >= ss.GOOD_ENOUGH                        # the ₹190 front rows are cheaper but poor


def test_a_total_budget_is_respected():
    seats = hall(**TIER)
    opts, msg = ss.rank_options(seats, 2, max_total=520)
    assert all(o.cand.total <= 520 for o in opts) and msg is None
    opts, msg = ss.rank_options(seats, 4, max_total=900)                     # 4 x gold = 1000: does not fit
    assert opts and all(o.cand.total <= 900 for o in opts) or (opts[0].over_budget_by and msg)


def test_a_budget_nothing_can_meet_gets_the_closest_option_and_an_honest_message():
    seats = hall(**TIER)
    opts, msg = ss.rank_options(seats, 2, max_total=100)
    assert len(opts) == 1 and opts[0].labels == ["Closest to your budget"]
    assert opts[0].over_budget_by == pytest.approx(opts[0].cand.total - 100)
    assert "Nothing fits your budget" in msg and any("over your budget" in w for w in opts[0].warnings)


def test_a_per_seat_price_cap_works_too():
    seats = hall(**TIER)
    opts, _ = ss.rank_options(seats, 2, max_price=200)
    assert opts and all(s.price <= 200 for o in opts for s in o.cand.seats)


def test_when_the_same_seats_win_every_way_other_rows_are_offered_to_compare():
    opts, _ = ss.rank_options(hall(rows="ABCDEFGHIJKLMNOPQ", cols=18), 2)
    assert len(opts) >= 2 and "Also good" in opts[1].labels
    assert len({o.cand.row for o in opts}) == len(opts)                     # genuinely different rows
    assert all(o.cand.visibility >= ss.GOOD_ENOUGH for o in opts)


def test_options_explain_themselves():
    o = ss.rank_options(hall(rows="ABCDEFGHIJKLMNOPQ", cols=18), 2)[0][0]
    text = " ".join(o.reasons)
    assert "centre" in text.lower() and "comfortable" in text and "₹100 each" in text


# ---- the endpoint --------------------------------------------------------------------------

def test_seat_options_endpoint_shape_and_validation(client):
    st = make_showtime_with_seats(client, rows=6, seats_per_row=10, price=100.0)
    sid = st["showtime_id"]
    r = client.get(f"/showtimes/{sid}/seat-options?count=2")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["options"] and body["message"] is None
    o = body["options"][0]
    assert len(o["seat_ids"]) == 2 and o["total_price"] == 200.0 and o["price_each"] == 100.0
    assert 0 <= o["visibility"] <= 100 and o["reasons"] and "Best view" in o["labels"]
    assert client.get(f"/showtimes/{sid}/seat-options?count=9").status_code == 422
    assert client.get(f"/showtimes/{sid}/seat-options?count=2&max_total=0").status_code == 422
    assert client.get("/showtimes/99999/seat-options").status_code == 404


def test_seat_options_endpoint_budget_and_sold_out(client):
    st = make_showtime_with_seats(client, rows=1, seats_per_row=3, price=100.0)
    sid = st["showtime_id"]
    r = client.get(f"/showtimes/{sid}/seat-options?count=2&max_total=50").json()
    assert "Nothing fits your budget" in r["message"] and r["options"][0]["over_budget_by"] == 150.0
    from tests.conftest import signup_and_login
    h = signup_and_login(client, "buyer@example.com")
    ids = [s["id"] for s in st["seats"]]
    client.post("/holds", json={"seat_ids": ids}, headers=h)
    assert "sold out" in client.get(f"/showtimes/{sid}/seat-options?count=1").json()["message"]
