"""Web Push reminders: subscriptions, the 'starts soon' job, no duplicates, dead devices."""
from datetime import datetime, timedelta, timezone

import pytest
from py_vapid import Vapid
from sqlalchemy import func, select

from app.models import Booking, PushSubscription, ReminderSent, Showtime
from app.services import push_service
from app.services.push_service import SubscriptionGone, send_due_reminders
from tests.conftest import book_seat_pending, make_showtime_with_seats, signup_and_login

SUB = {"endpoint": "https://push.example.com/send/abc123", "keys": {"p256dh": "BPubKeyKeyKeyKeyKey", "auth": "authsecret"}}


@pytest.fixture()
def sent(monkeypatch):
    """Replace the real push sender; record (endpoint, payload) instead."""
    log = []
    monkeypatch.setattr(push_service, "_sender", lambda sub, payload: log.append((sub.endpoint, payload)))
    return log


def _paid_booking_starting_in(client, db, minutes, email="a@example.com", subscribe=True):
    h = signup_and_login(client, email)
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2, price=10.0)
    b = book_seat_pending(client, h, st["seats"][0]["id"])
    assert client.post("/payments", json={"booking_id": b["id"]}, headers={**h, "Idempotency-Key": f"k-{email}"}).status_code == 201
    showtime = db.get(Showtime, st["showtime_id"])
    showtime.starts_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    db.commit()
    if subscribe:
        assert client.post("/push/subscribe", json=SUB, headers=h).status_code == 201
    return h, b["id"]


def test_vapid_keys_are_valid_and_can_sign():
    keys = push_service.get_keys()
    assert len(keys["public"]) == 87                          # an uncompressed P-256 point, base64url
    headers = Vapid.from_raw(keys["private"].encode()).sign({"sub": "mailto:a@example.com", "aud": "https://push.example.com"})
    assert headers["Authorization"].startswith("vapid ")
    assert push_service.get_keys() == keys                    # stable between calls


def test_public_key_endpoint_needs_no_login(client):
    assert client.get("/push/public-key").json()["public_key"] == push_service.get_keys()["public"]


def test_subscribe_and_unsubscribe(client, db):
    h = signup_and_login(client, "a@example.com")
    other = signup_and_login(client, "b@example.com")
    assert client.post("/push/subscribe", json=SUB).status_code == 401
    assert client.post("/push/subscribe", json={**SUB, "endpoint": "http://insecure.example.com/x"}, headers=h).status_code == 422
    assert client.post("/push/subscribe", json=SUB, headers=h).status_code == 201
    assert client.post("/push/subscribe", json=SUB, headers=h).status_code == 201     # repeat: no duplicate row
    assert db.scalar(select(func.count()).select_from(PushSubscription)) == 1
    # someone else cannot remove my device
    client.request("DELETE", "/push/subscribe", json={"endpoint": SUB["endpoint"]}, headers=other)
    assert db.scalar(select(func.count()).select_from(PushSubscription)) == 1
    client.request("DELETE", "/push/subscribe", json={"endpoint": SUB["endpoint"]}, headers=h)
    assert db.scalar(select(func.count()).select_from(PushSubscription)) == 0


def test_reminder_goes_out_once_with_theatre_and_directions(client, db, sent):
    _, booking_id = _paid_booking_starting_in(client, db, minutes=10)
    assert send_due_reminders(db) == 1
    endpoint, payload = sent[0]
    assert endpoint == SUB["endpoint"]
    assert "Test Movie starts in" in payload["title"] and "Test Theatre" in payload["body"]
    assert payload["url"] == "https://www.google.com/maps/dir/?api=1&destination=Test%20Theatre%2C%20Testville"
    assert send_due_reminders(db) == 0                                    # never twice
    assert len(sent) == 1
    assert db.scalar(select(func.count()).select_from(ReminderSent)) == 1


def test_no_reminder_too_early_too_late_or_for_unpaid(client, db, sent):
    _paid_booking_starting_in(client, db, minutes=30, email="early@example.com")
    _paid_booking_starting_in(client, db, minutes=-5, email="late@example.com")       # already started
    h = signup_and_login(client, "unpaid@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2)
    b = book_seat_pending(client, h, st["seats"][0]["id"])                            # never paid
    client.post("/push/subscribe", json={**SUB, "endpoint": "https://push.example.com/send/unpaid"}, headers=h)
    show = db.get(Showtime, st["showtime_id"]); show.starts_at = datetime.now(timezone.utc) + timedelta(minutes=5); db.commit()
    assert send_due_reminders(db) == 0 and sent == []


def test_user_without_a_device_is_not_marked_so_subscribing_later_still_works(client, db, sent):
    h, booking_id = _paid_booking_starting_in(client, db, minutes=10, subscribe=False)
    assert send_due_reminders(db) == 0
    assert db.scalar(select(func.count()).select_from(ReminderSent)) == 0
    client.post("/push/subscribe", json=SUB, headers=h)
    assert send_due_reminders(db) == 1


def test_dead_device_is_forgotten_and_failed_sends_are_retried(client, db, monkeypatch):
    h, _ = _paid_booking_starting_in(client, db, minutes=10)

    def gone(sub, payload):
        raise SubscriptionGone()
    monkeypatch.setattr(push_service, "_sender", gone)
    assert send_due_reminders(db) == 0
    assert db.scalar(select(func.count()).select_from(PushSubscription)) == 0        # uninstalled/expired: removed
    # a device that fails for a temporary reason is kept, and the reminder is retried
    client.post("/push/subscribe", json=SUB, headers=h)

    def flaky(sub, payload):
        raise RuntimeError("push server 503")
    monkeypatch.setattr(push_service, "_sender", flaky)
    assert send_due_reminders(db) == 0
    assert db.scalar(select(func.count()).select_from(PushSubscription)) == 1
    assert db.scalar(select(func.count()).select_from(ReminderSent)) == 0
    ok = []
    monkeypatch.setattr(push_service, "_sender", lambda sub, payload: ok.append(1))
    assert send_due_reminders(db) == 1 and ok


def test_test_notification_endpoint(client, db, sent):
    h = signup_and_login(client, "a@example.com")
    assert client.post("/push/test", headers=h).status_code == 409           # no device yet
    client.post("/push/subscribe", json=SUB, headers=h)
    r = client.post("/push/test", headers=h)
    assert r.status_code == 200 and r.json()["sent"] == 1 and sent[0][1]["title"] == "Reminders are on"


def test_real_sender_encrypts_signs_and_a_device_can_read_it():
    """No fake here: run the REAL send path against a local stand-in for a push
    server, then decrypt the message the way a browser would."""
    import base64, http.server, json, os, threading
    from types import SimpleNamespace

    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    device_key = ec.generate_private_key(ec.SECP256R1())          # what the browser keeps
    auth_secret = os.urandom(16)
    p256dh = device_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got["headers"] = dict(self.headers)
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    sub = SimpleNamespace(endpoint=f"http://127.0.0.1:{server.server_port}/push/xyz", p256dh=b64(p256dh), auth=b64(auth_secret))
    try:
        push_service._real_send(sub, {"title": "418 starts in 15 min", "body": "Have you reached Grand Cinema?"})
    finally:
        server.server_close()

    headers = {k.lower(): v for k, v in got["headers"].items()}                      # header names are case-insensitive
    assert headers["authorization"].startswith("vapid ")                             # signed with our key
    assert headers["content-encoding"] == "aes128gcm"                                # encrypted
    assert b"418 starts" not in got["body"]                                          # not readable on the wire
    plain = http_ece.decrypt(got["body"], private_key=device_key, auth_secret=auth_secret, version="aes128gcm")
    assert json.loads(plain)["title"] == "418 starts in 15 min"                      # but the device can read it
