"""Sending one-time codes: email (real SMTP against a local mail server), SMS
providers (HTTP calls captured), failures, and the demo fallback."""
import re
import socketserver
import threading

import httpx
import pytest

from app.config import settings
from app.services import otp_delivery


@pytest.fixture(autouse=True)
def _restore_otp_settings():
    keys = ["otp_dev_mode", "otp_sms_provider", "twilio_account_sid", "twilio_auth_token", "twilio_from",
            "msg91_auth_key", "msg91_template_id", "smtp_host", "smtp_port", "smtp_user", "smtp_password",
            "smtp_from", "smtp_starttls"]
    saved = {k: getattr(settings, k) for k in keys}
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


class _MailSink:
    """A tiny SMTP server that records every message it is given."""
    def __init__(self):
        self.messages: list[str] = []
        sink = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda line: self.wfile.write(line.encode() + b"\r\n")
                w("220 sink ready")
                data_mode, body = False, []
                for raw in self.rfile:
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if data_mode:
                        if line == ".":
                            sink.messages.append("\n".join(body)); data_mode = False; w("250 queued")
                        else:
                            body.append(line)
                    elif line.upper().startswith(("EHLO", "HELO")):
                        w("250 sink")
                    elif line.upper().startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                        w("250 ok")
                    elif line.upper() == "DATA":
                        data_mode = True; body = []; w("354 go ahead")
                    elif line.upper() == "QUIT":
                        w("221 bye"); return
                    else:
                        w("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


@pytest.fixture()
def mail():
    sink = _MailSink()
    settings.smtp_host, settings.smtp_port, settings.smtp_from = "127.0.0.1", sink.port, "no-reply@ticketshow.test"
    settings.smtp_starttls, settings.smtp_user = False, ""
    yield sink
    sink.close()


def test_mask_hides_most_of_the_destination():
    assert otp_delivery.mask("+919876543210") == "+91******3210"
    assert otp_delivery.mask("swathi@gmail.com") == "sw****@gmail.com"


def test_email_code_is_really_sent_and_signs_in(client, mail):
    settings.otp_dev_mode = False                                   # production-like: the code must NOT be in the reply
    r = client.post("/auth/otp/request", json={"email": "Fan@Example.com"})
    assert r.status_code == 200 and r.json()["channel"] == "email" and r.json()["dev_otp"] is None
    assert r.json()["destination"] == "fa**@example.com"
    assert len(mail.messages) == 1
    text = mail.messages[0]
    assert "To: fan@example.com" in text and "Ticketshow" in text
    code = re.search(r"code is (\d{6})", text).group(1)             # what the person reads in their inbox
    tok = client.post("/auth/otp/verify", json={"email": "fan@example.com", "code": code})
    assert tok.status_code == 200
    me = client.get("/me", headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).json()
    assert me["email"] == "fan@example.com" and me["phone"] is None
    assert client.post("/auth/otp/verify", json={"email": "fan@example.com", "code": code}).status_code == 401  # single use


def test_email_code_logs_into_an_existing_password_account(client, mail):
    from tests.conftest import signup_and_login
    signup_and_login(client, "owner@example.com")
    me_before = client.post("/auth/login", json={"email": "owner@example.com", "password": "password123"}).json()["access_token"]
    uid = client.get("/me", headers={"Authorization": f"Bearer {me_before}"}).json()["id"]
    client.post("/auth/otp/request", json={"email": "owner@example.com"})
    code = re.search(r"code is (\d{6})", mail.messages[0]).group(1)
    tok = client.post("/auth/otp/verify", json={"email": "owner@example.com", "code": code}).json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"] == uid     # same person, not a duplicate


def test_must_give_exactly_one_of_phone_or_email(client):
    assert client.post("/auth/otp/request", json={}).status_code == 422
    assert client.post("/auth/otp/request", json={"phone": "9876543210", "email": "a@example.com"}).status_code == 422
    assert client.post("/auth/otp/request", json={"email": "not-an-email"}).status_code == 422
    assert client.post("/auth/otp/verify", json={"email": "a@example.com", "phone": "9876543210", "code": "123456"}).status_code == 422


def test_nothing_configured_and_demo_mode_off_is_a_clear_503(client):
    settings.otp_dev_mode = False
    assert client.post("/auth/otp/request", json={"phone": "9876543210"}).status_code == 503
    assert client.post("/auth/otp/request", json={"email": "a@example.com"}).status_code == 503


def test_demo_mode_still_shows_the_code_on_screen_when_nothing_is_configured(client):
    r = client.post("/auth/otp/request", json={"email": "demo@example.com"}).json()
    assert r["channel"] == "screen" and len(r["dev_otp"]) == 6


@pytest.fixture()
def captured(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return httpx.Response(200 if "msg91" in url else 201, json={"type": "success"})
    monkeypatch.setattr(otp_delivery.httpx, "post", fake_post)
    return calls


def test_twilio_sms_is_sent_with_the_code(client, captured):
    settings.otp_dev_mode, settings.otp_sms_provider = False, "twilio"
    settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from = "ACtest", "secret-token", "+15005550006"
    r = client.post("/auth/otp/request", json={"phone": "9876543210"})
    assert r.status_code == 200 and r.json()["channel"] == "sms" and r.json()["dev_otp"] is None
    call = captured[0]
    assert call["url"].endswith("/Accounts/ACtest/Messages.json") and call["auth"] == ("ACtest", "secret-token")
    assert call["data"]["To"] == "+9876543210" and call["data"]["From"] == "+15005550006"
    code = re.search(r"code is (\d{6})", call["data"]["Body"]).group(1)
    assert client.post("/auth/otp/verify", json={"phone": "9876543210", "code": code}).status_code == 200


def test_twilio_messaging_service_sid_is_used_when_given(client, captured):
    settings.otp_dev_mode, settings.otp_sms_provider = False, "twilio"
    settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from = "ACtest", "t", "MGabc123"
    client.post("/auth/otp/request", json={"phone": "9876543210"})
    assert captured[0]["data"]["MessagingServiceSid"] == "MGabc123" and "From" not in captured[0]["data"]


def test_msg91_otp_request_is_built_correctly(client, captured):
    settings.otp_dev_mode, settings.otp_sms_provider = False, "msg91"
    settings.msg91_auth_key, settings.msg91_template_id = "key123", "tmpl9"
    captured_status = client.post("/auth/otp/request", json={"phone": "+919876543210"}).status_code
    assert captured_status == 200
    p = captured[0]["params"]
    assert p["mobile"] == "919876543210" and p["template_id"] == "tmpl9" and p["authkey"] == "key123" and len(p["otp"]) == 6


def test_provider_rejecting_the_message_is_a_502_and_the_code_is_discarded(client, monkeypatch, caplog):
    settings.otp_dev_mode, settings.otp_sms_provider = False, "twilio"
    settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from = "ACtest", "secret-token", "+15005550006"
    monkeypatch.setattr(otp_delivery.httpx, "post", lambda url, **kw: httpx.Response(400, json={"message": "bad"}))
    r = client.post("/auth/otp/request", json={"phone": "9876543210"})
    assert r.status_code == 502
    assert client.post("/auth/otp/verify", json={"phone": "9876543210", "code": "000000"}).status_code == 401
    assert "secret-token" not in caplog.text                         # secrets never reach the logs


def test_delivery_failure_in_demo_mode_falls_back_to_the_screen_code(client, monkeypatch):
    settings.otp_sms_provider = "twilio"
    settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from = "ACtest", "t", "+1500"
    def boom(url, **kw):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(otp_delivery.httpx, "post", boom)
    r = client.post("/auth/otp/request", json={"phone": "9876543210"}).json()
    assert r["channel"] == "screen" and len(r["dev_otp"]) == 6
    assert client.post("/auth/otp/verify", json={"phone": "9876543210", "code": r["dev_otp"]}).status_code == 200


def test_unreachable_mail_server_is_a_502_when_not_in_demo_mode(client):
    settings.otp_dev_mode = False
    settings.smtp_host, settings.smtp_port, settings.smtp_from, settings.smtp_starttls = "127.0.0.1", 1, "a@b.test", False
    assert client.post("/auth/otp/request", json={"email": "a@example.com"}).status_code == 502
