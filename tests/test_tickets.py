"""E-tickets and the confirmation email (app/services/ticket_service.py, ticket_email.py, routers/tickets.py)."""

import email
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Booking
from app.services import ticket_email, ticket_service
from tests.conftest import admin_headers, book_seat_pending, make_showtime_with_seats, signup_and_login
from tests.test_otp_delivery import _MailSink

SHOW_START = datetime(2030, 1, 1, 12, 30, tzinfo=UTC)  # make_showtime_with_seats' show: 18:00 India time


@pytest.fixture(autouse=True)
def _settings_and_inline_email():
    keys = ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_starttls"]
    saved = {k: getattr(settings, k) for k in keys}
    settings.smtp_host = ""  # email off unless a test turns it on
    ticket_email.RUN_IN_THREAD = False  # send before the call returns, so tests can check it
    yield
    ticket_email.RUN_IN_THREAD = True
    for k, v in saved.items():
        setattr(settings, k, v)


@pytest.fixture()
def mail():
    sink = _MailSink()
    settings.smtp_host, settings.smtp_port, settings.smtp_from = "127.0.0.1", sink.port, "no-reply@ticketshow.test"
    settings.smtp_starttls, settings.smtp_user = False, ""
    yield sink
    sink.close()


def _paid_booking(client, email_addr="fan@example.com", key="pay-1"):
    h = signup_and_login(client, email_addr)
    seat_id = make_showtime_with_seats(client, rows=1, seats_per_row=2)["seats"][0]["id"]
    booking = book_seat_pending(client, h, seat_id)
    r = client.post("/payments", json={"booking_id": booking["id"]}, headers={**h, "Idempotency-Key": key})
    assert r.status_code == 201, r.text
    return h, booking["id"]


def _parse(raw: str):
    return email.message_from_string(raw)


# ---------- codes ----------


def test_codes_are_random_readable_and_normalize():
    codes = {ticket_service.new_code() for _ in range(200)}
    assert len(codes) == 200
    for c in codes:
        assert len(c) == 14 and c[4] == c[9] == "-"
        assert not set(c.replace("-", "")) & set("01IO")
    c = next(iter(codes))
    assert ticket_service.normalize(c.lower().replace("-", " ")) == c
    assert ticket_service.normalize("short") == ""
    assert ticket_service.normalize("OOOO-OOOO-OOOO") == ""  # letters that are not in the alphabet


# ---------- the owner's ticket ----------


def test_paying_issues_a_ticket_with_a_qr_code(client):
    h, booking_id = _paid_booking(client)
    r = client.get(f"/bookings/{booking_id}/ticket", headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["state"] == "valid"
    assert t["qr_image"].startswith("data:image/svg+xml")
    assert t["ticket_url"].endswith(f"/ticket/{t['ticket_code']}")
    assert t["seat_labels"] == ["A1"] and t["total_cents"] == 1000
    assert "google.com/maps" in t["directions_url"]
    # the same code every time
    assert client.get(f"/bookings/{booking_id}/ticket", headers=h).json()["ticket_code"] == t["ticket_code"]


def test_unpaid_booking_has_no_ticket(client):
    h = signup_and_login(client, "fan@example.com")
    seat_id = make_showtime_with_seats(client)["seats"][0]["id"]
    booking = book_seat_pending(client, h, seat_id)
    assert client.get(f"/bookings/{booking['id']}/ticket", headers=h).status_code == 409


def test_only_the_owner_sees_the_full_ticket(client):
    _, booking_id = _paid_booking(client)
    other = signup_and_login(client, "someone-else@example.com")
    assert client.get(f"/bookings/{booking_id}/ticket", headers=other).status_code == 403
    assert client.get(f"/bookings/{booking_id}/ticket").status_code == 401


def test_old_paid_booking_gets_a_code_when_opened(client, db):
    h, booking_id = _paid_booking(client)
    db.get(Booking, booking_id).ticket_code = None
    db.commit()
    code = client.get(f"/bookings/{booking_id}/ticket", headers=h).json()["ticket_code"]
    assert ticket_service.normalize(code) == code


def test_calendar_file(client):
    h, booking_id = _paid_booking(client)
    r = client.get(f"/bookings/{booking_id}/ticket.ics", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "BEGIN:VEVENT" in body and "DTSTART:20300101T123000Z" in body
    assert "DTEND:20300101T143000Z" in body  # 120-minute movie
    assert "Test Movie at Test Theatre" in body and "TRIGGER:-PT60M" in body


# ---------- the public page a QR scan opens ----------


def test_public_view_shows_the_show_but_nothing_personal(client):
    h, booking_id = _paid_booking(client)
    code = client.get(f"/bookings/{booking_id}/ticket", headers=h).json()["ticket_code"]
    r = client.get(f"/tickets/{code.lower()}")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "valid" and body["movie_title"] == "Test Movie"
    assert "fan@example.com" not in r.text and "user_id" not in body and "total_cents" not in body
    assert client.get("/tickets/AAAA-BBBB-CCCC").status_code == 404
    assert client.get("/tickets/not-a-code").status_code == 404


# ---------- check-in at the door ----------


def _code(client, h, booking_id):
    return client.get(f"/bookings/{booking_id}/ticket", headers=h).json()["ticket_code"]


def test_check_in_needs_staff(client):
    h, booking_id = _paid_booking(client)
    code = _code(client, h, booking_id)
    assert client.post(f"/tickets/{code}/check-in", headers=h).status_code == 403
    r = client.post(f"/tickets/{code}/check-in", headers=admin_headers(client, "door@example.com"))
    assert r.status_code == 200 and r.json()["result"] == "too_early"  # the show is in 2030


def test_check_in_admits_once(client, db):
    h, booking_id = _paid_booking(client)
    code = _code(client, h, booking_id)
    now = SHOW_START - timedelta(minutes=20)
    assert ticket_service.check_in(db, code, now).result == "admitted"
    again = ticket_service.check_in(db, code, now + timedelta(minutes=1))
    assert again.result == "already_used" and "Do not admit" in again.message
    assert client.get(f"/tickets/{code}").json()["state"] == "used"
    assert client.get(f"/bookings/{booking_id}/ticket", headers=h).json()["state"] == "used"


def test_check_in_time_window(client, db):
    h, booking_id = _paid_booking(client)
    code = _code(client, h, booking_id)
    assert ticket_service.check_in(db, code, SHOW_START - timedelta(hours=4)).result == "too_early"
    assert ticket_service.check_in(db, code, SHOW_START + timedelta(hours=3, minutes=5)).result == "expired"
    assert ticket_service.check_in(db, code, SHOW_START + timedelta(hours=2, minutes=30)).result == "admitted"
    assert ticket_service.check_in(db, "nonsense", SHOW_START).result == "invalid"


def test_cancelled_booking_is_refused_at_the_door(client, db):
    h, booking_id = _paid_booking(client)
    code = _code(client, h, booking_id)
    db.get(Booking, booking_id).status = "cancelled"
    db.commit()
    assert ticket_service.check_in(db, code, SHOW_START).result == "cancelled"
    assert client.get(f"/tickets/{code}").json()["state"] == "cancelled"


def test_two_scanners_at_the_same_moment_admit_once(client):
    h, booking_id = _paid_booking(client)
    code = _code(client, h, booking_id)
    results, barrier = [], threading.Barrier(4)

    def scan():
        with SessionLocal() as s:
            barrier.wait()
            results.append(ticket_service.check_in(s, code, SHOW_START).result)

    threads = [threading.Thread(target=scan) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["admitted", "already_used", "already_used", "already_used"]


# ---------- the confirmation email ----------


def test_confirmation_email_is_sent_once_with_qr_and_calendar(client, mail, db):
    _, booking_id = _paid_booking(client)
    assert len(mail.messages) == 1
    msg = _parse(mail.messages[0])
    assert msg["To"] == "fan@example.com" and "Test Movie" in msg["Subject"]
    types = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types and "text/html" in types and "image/png" in types and "text/calendar" in types
    html_part = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_payload(decode=True).decode()
    code = db.get(Booking, booking_id).ticket_code
    assert "cid:ticket-qr" in html_part and code in html_part
    # Every path that settles a payment again (sweeper, retries) must not send a second one.
    assert ticket_email.send_confirmation(db, booking_id) is False
    assert ticket_email.send_pending_confirmations(db) == 0
    assert len(mail.messages) == 1


def test_movie_names_are_escaped_in_the_email(client, db):
    _, booking_id = _paid_booking(client)
    booking = db.get(Booking, booking_id)
    booking.showtime.movie.title = "<script>alert(1)</script>"
    db.flush()
    msg = ticket_email.build_message(booking, "fan@example.com")
    html_part = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_content()
    assert "<script>" not in html_part and "&lt;script&gt;" in html_part
    db.rollback()


def test_no_email_without_smtp_and_payment_still_works(client, db):
    _, booking_id = _paid_booking(client)
    b = db.get(Booking, booking_id)
    assert b.status == "confirmed" and b.ticket_code and b.confirmation_emailed_at is None


def test_mail_server_down_does_not_break_payment_and_sweeper_retries(client, db):
    settings.smtp_host, settings.smtp_port, settings.smtp_from = "127.0.0.1", 1, "no-reply@ticketshow.test"
    settings.smtp_starttls, settings.smtp_user = False, ""
    _, booking_id = _paid_booking(client)  # payment still succeeds
    assert db.get(Booking, booking_id).confirmation_emailed_at is None  # claim was given back

    sink = _MailSink()
    try:
        settings.smtp_port = sink.port
        assert ticket_email.send_pending_confirmations(db) == 1
        assert len(sink.messages) == 1
        assert ticket_email.send_pending_confirmations(db) == 0
    finally:
        sink.close()


def test_sweeper_skips_old_bookings(client, mail, db):
    settings.smtp_host = ""
    _, booking_id = _paid_booking(client)
    settings.smtp_host = "127.0.0.1"
    later = datetime.now(UTC) + timedelta(days=2)
    assert ticket_email.send_pending_confirmations(db, now=later) == 0
    assert ticket_email.send_pending_confirmations(db) == 1


def test_resend_button(client, mail):
    h, booking_id = _paid_booking(client)
    assert len(mail.messages) == 1
    r = client.post(f"/bookings/{booking_id}/ticket/email", headers=h)
    assert r.status_code == 200 and r.json()["sent_to"] == "fan@example.com"
    assert len(mail.messages) == 2
    for _ in range(2):
        client.post(f"/bookings/{booking_id}/ticket/email", headers=h)
    assert client.post(f"/bookings/{booking_id}/ticket/email", headers=h).status_code == 429


def test_resend_without_email_setup_says_so(client):
    h, booking_id = _paid_booking(client)
    assert client.post(f"/bookings/{booking_id}/ticket/email", headers=h).status_code == 503


def test_phone_only_account_gets_no_email(client, mail, db):
    from app.models import User

    _, booking_id = _paid_booking(client, email_addr="phone-user@example.com")
    mail.messages.clear()
    booking = db.get(Booking, booking_id)
    user = db.get(User, booking.user_id)
    user.email, user.phone = None, "+919876543210"
    booking.confirmation_emailed_at = None
    db.commit()
    assert ticket_email.send_confirmation(db, booking_id) is False
    assert ticket_email.send_pending_confirmations(db) == 0
    assert mail.messages == []
