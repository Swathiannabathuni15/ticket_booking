"""Things that matter the moment the site is on the public internet."""
import pytest

from app.config import Settings, settings
from app.jobs import inprocess
from tests.conftest import _test_redis, admin_headers


def _prod(**over):
    base = dict(environment="production", jwt_secret="x" * 48, payment_webhook_secret="y" * 48,
                otp_dev_mode=False, cors_origin_regex="", gateway_allow_test_methods=False,
                public_base_url="https://tickets.example.com")
    return Settings(_env_file=None, **{**base, **over})


def test_a_correct_production_config_has_no_problems():
    assert _prod().production_problems() == []


@pytest.mark.parametrize("over,needle", [
    ({"jwt_secret": "dev-only-secret-change-me-in-production-0123456789"}, "JWT_SECRET"),
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"payment_webhook_secret": "dev-webhook-secret-change-me-0123456789"}, "PAYMENT_WEBHOOK_SECRET"),
    ({"otp_dev_mode": True}, "OTP_DEV_MODE"),
    ({"gateway_allow_test_methods": True}, "GATEWAY_ALLOW_TEST_METHODS"),
    ({"cors_origin_regex": r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"}, "CORS_ORIGIN_REGEX"),
    ({"public_base_url": "http://localhost:5173"}, "PUBLIC_BASE_URL"),
])
def test_each_insecure_production_setting_is_reported(over, needle):
    problems = _prod(**over).production_problems()
    assert any(needle in p for p in problems), problems


def test_the_server_refuses_to_start_in_production_with_dev_secrets(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(settings, "environment", "production")     # the dev secrets are still the defaults here
    with pytest.raises(RuntimeError, match="Refusing to start in production"):
        with TestClient(app):
            pass


def test_host_database_urls_are_converted_for_our_driver():
    assert Settings(_env_file=None, database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    assert Settings(_env_file=None, database_url="postgresql://u:p@h/db?sslmode=require").database_url == \
        "postgresql+psycopg://u:p@h/db?sslmode=require"
    assert Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"


def test_a_code_can_never_open_an_admin_account(client):
    """Otherwise 'request a code for the admin's email' would be an account takeover."""
    admin_headers(client, "boss@example.com")
    code = client.post("/auth/otp/request", json={"email": "boss@example.com"}).json()["dev_otp"]
    r = client.post("/auth/otp/verify", json={"email": "boss@example.com", "code": code})
    assert r.status_code == 403 and "password" in r.json()["detail"]
    # ...while the password still works
    assert client.post("/auth/login", json={"email": "boss@example.com", "password": "password123"}).status_code == 200


def test_background_round_runs_the_jobs_and_only_one_process_wins_the_lock():
    r = _test_redis()
    first = inprocess.tick(r)
    assert set(first) == {"reconciled", "expired", "reminders", "ticket_emails", "scheduled"} and "skipped" not in first
    assert inprocess.tick(r) == {"skipped": True}                  # a second server in the same moment does nothing
    r.delete(inprocess.LOCK_KEY)
    assert "skipped" not in inprocess.tick(r)
