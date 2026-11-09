"""Split the bill (A5): rounding, wrong totals, double pay, expired and
tampered links, owner cancel."""
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.config import settings
from app.models import LedgerEntry
from app.security import create_share_token
from tests.conftest import book_seats_pending, make_showtime_with_seats, signup_and_login


def _paid_booking(client, n_seats=1, price=10.0, email="owner@example.com"):
    h = signup_and_login(client, email)
    st = make_showtime_with_seats(client, rows=1, seats_per_row=max(n_seats, 2), price=price)
    ids = [s["id"] for s in st["seats"][:n_seats]]
    booking = book_seats_pending(client, h, ids)
    r = client.post("/payments", json={"booking_id": booking["id"]}, headers={**h, "Idempotency-Key": f"k-{email}"})
    assert r.status_code == 201
    return h, booking["id"]


def _token(link: str) -> str:
    return link.rsplit("/", 1)[1]


def test_equal_split_rounding_adds_up(client):
    h, bid = _paid_booking(client, price=10.0)  # 1000 cents / 3
    r = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann", "Bob"]}, headers=h)
    assert r.status_code == 201, r.text
    amounts = [s["amount_cents"] for s in r.json()["shares"]]
    assert amounts == [334, 333, 333]
    assert sum(amounts) == r.json()["total_cents"] == 1000
    assert r.json()["shares"][0]["is_owner"] and r.json()["shares"][0]["status"] == "paid"


def test_custom_split_wrong_total_rejected(client):
    h, bid = _paid_booking(client, price=10.0)
    r = client.post(
        f"/bookings/{bid}/split",
        json={"friends": ["Ann"], "mode": "custom", "custom_cents": [500, 400]}, headers=h,
    )
    assert r.status_code == 422
    r = client.post(
        f"/bookings/{bid}/split",
        json={"friends": ["Ann"], "mode": "custom", "custom_cents": [600, 400]}, headers=h,
    )
    assert r.status_code == 201


def test_cannot_split_unpaid_booking_or_someone_elses(client):
    h = signup_and_login(client, "a@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2)
    b = book_seats_pending(client, h, [st["seats"][0]["id"]])
    assert client.post(f"/bookings/{b['id']}/split", json={"friends": ["x"]}, headers=h).status_code == 409
    other = signup_and_login(client, "b@example.com")
    assert client.post(f"/bookings/{b['id']}/split", json={"friends": ["x"]}, headers=other).status_code == 403


def test_split_twice_rejected(client):
    h, bid = _paid_booking(client)
    assert client.post(f"/bookings/{bid}/split", json={"friends": ["A"]}, headers=h).status_code == 201
    assert client.post(f"/bookings/{bid}/split", json={"friends": ["A"]}, headers=h).status_code == 409


def test_friend_pays_without_account_and_split_settles(client):
    h, bid = _paid_booking(client)
    split = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann"]}, headers=h).json()
    link = split["shares"][1]["link"]
    view = client.get(link)
    assert view.status_code == 200 and view.json()["amount_cents"] == 500
    assert "email" not in view.text
    pay = client.post(link)  # note: no Authorization header
    assert pay.status_code == 200 and pay.json()["status"] == "paid"
    after = client.get(f"/bookings/{bid}/split", headers=h).json()
    assert after["status"] == "settled" and after["paid_cents"] == 1000


def test_double_pay_is_idempotent(client, db):
    h, bid = _paid_booking(client)
    link = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann"]}, headers=h).json()["shares"][1]["link"]
    assert client.post(link).status_code == 200
    assert client.post(link).status_code == 200
    n = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "payment"))
    assert n == 2  # owner's share + Ann's, Ann's only once


def test_concurrent_double_pay_charges_once(client, db):
    h, bid = _paid_booking(client)
    link = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann"]}, headers=h).json()["shares"][1]["link"]
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(lambda _: client.post(link).status_code, range(8)))
    assert set(codes) == {200}
    n = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "payment"))
    assert n == 2


def test_expired_link(client):
    h, bid = _paid_booking(client)
    share = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann"]}, headers=h).json()["shares"][1]
    from app.db import SessionLocal
    from app.models import SplitShare
    with SessionLocal() as s:
        nonce = s.get(SplitShare, share["id"]).nonce
    expired = create_share_token(share["id"], nonce, ttl_hours=-1)
    assert client.post(f"/pay/share/{expired}").status_code == 410
    assert client.get(f"/pay/share/{expired}").status_code == 410


def test_tampered_and_garbage_tokens(client):
    h, bid = _paid_booking(client)
    link = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann"]}, headers=h).json()["shares"][1]["link"]
    token = _token(link)
    head, payload, sig = token.split(".")
    flipped = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    assert client.post(f"/pay/share/{head}.{payload}.{flipped}").status_code == 404
    assert client.post(f"/pay/share/{head}.{payload}.").status_code == 404
    assert client.post("/pay/share/not-a-token").status_code == 404
    # A login token is not a share token.
    login_token = h["Authorization"].split()[1]
    assert client.post(f"/pay/share/{login_token}").status_code == 404
    # A correctly signed token with the wrong nonce is rejected too.
    assert client.post(f"/pay/share/{create_share_token(1, 'wrongnonce')}").status_code == 404


def test_owner_cancels_split_refunds_and_blocks_payment(client, db):
    h, bid = _paid_booking(client, n_seats=2)
    split = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann", "Bob"]}, headers=h).json()
    ann, bob = split["shares"][1]["link"], split["shares"][2]["link"]
    assert client.post(ann).status_code == 200
    r = client.delete(f"/bookings/{bid}/split", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(bob).status_code == 409
    refunds = db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "refund"))
    assert refunds == 1  # only Ann had paid


def test_cancelling_the_booking_closes_the_split(client):
    h, bid = _paid_booking(client)
    link = client.post(f"/bookings/{bid}/split", json={"friends": ["Ann"]}, headers=h).json()["shares"][1]["link"]
    assert client.delete(f"/bookings/{bid}", headers=h).status_code == 200
    assert client.post(link).status_code == 409


def test_share_pay_rate_limited(client):
    settings.rate_limit_share_pay_per_window = 2
    assert client.post("/pay/share/junk").status_code == 404
    assert client.post("/pay/share/junk").status_code == 404
    assert client.post("/pay/share/junk").status_code == 429
