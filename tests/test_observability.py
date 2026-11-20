"""Request ids, consistent error bodies, rate-limit headers, /api/v1, structured logs, Sentry scrubbing."""

import json
import logging

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.observability import JsonFormatter, request_id_var, scrub_event
from tests.conftest import signup_and_login


def test_every_response_has_a_request_id_and_errors_quote_it(client):
    r = client.get("/api/v1/bookings/me")
    rid = r.headers["x-request-id"]
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or missing token", "code": "not_signed_in", "request_id": rid}
    # A sane id sent by the caller (or a proxy) is kept, so logs can be followed across services.
    assert client.get("/health", headers={"X-Request-ID": "trace-abc-123"}).headers["x-request-id"] == "trace-abc-123"
    # Junk is replaced, never echoed.
    assert client.get("/health", headers={"X-Request-ID": "<script>"}).headers["x-request-id"] != "<script>"


def test_error_codes_are_consistent(client):
    h = signup_and_login(client, "a@example.com")
    assert client.get("/api/v1/nope").json()["code"] == "not_found"
    v = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"}).json()
    assert v["code"] == "validation_error" and isinstance(v["detail"], list)
    assert client.get("/api/v1/admin/dashboard", headers=h).json()["code"] == "forbidden"


def test_a_crash_returns_a_clean_500_with_the_request_id():
    def boom():
        raise RuntimeError("secret internal detail")

    app.add_api_route("/__boom", boom)
    try:
        r = TestClient(app, raise_server_exceptions=False).get("/__boom")
    finally:
        app.router.routes.pop()
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error" and body["request_id"] == r.headers["x-request-id"]
    assert "secret internal detail" not in r.text and "Traceback" not in r.text


def test_rate_limit_headers(client):
    signup_and_login(client, "a@example.com")
    r = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "password123"})
    limit = settings.rate_limit_login_per_window
    assert r.headers["ratelimit-limit"] == str(limit)
    assert int(r.headers["ratelimit-remaining"]) < limit
    assert 0 < int(r.headers["ratelimit-reset"]) <= settings.rate_limit_window_seconds
    # The counter is a fixed one-minute window, which may roll over mid-test and start again from 0:
    # so keep going (at most two windows' worth) until the first refusal.
    for _ in range(2 * limit + 2):
        r = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-password"})
        if r.status_code == 429:
            break
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert r.headers["ratelimit-remaining"] == "0" and int(r.headers["retry-after"]) > 0


def test_versioned_paths_and_deprecated_old_ones(client):
    assert client.get("/api/v1/movies").status_code == 200
    old = client.get("/movies")
    assert old.status_code == 200 and old.headers["deprecation"] == "true"
    assert "deprecation" not in client.get("/api/v1/movies").headers
    paths = app.openapi()["paths"]
    assert paths and all(p.startswith("/api/v1/") for p in paths)  # the docs show only the versioned API


def test_gateway_limit_counts_versioned_paths(client):
    h = signup_and_login(client, "a@example.com")
    settings.rate_limit_user_per_second = 2
    responses = [client.get("/api/v1/bookings/me", headers=h) for _ in range(5)]
    refused = [r for r in responses if r.status_code == 429]
    assert refused, [r.status_code for r in responses]
    body = refused[0].json()
    assert body["code"] == "rate_limited" and body["request_id"] == refused[0].headers["x-request-id"]
    assert int(refused[0].headers["retry-after"]) >= 1


def test_json_logs_carry_the_request_id_and_extra_fields():
    rec = logging.LogRecord("http.access", logging.INFO, "", 0, "GET %s", ("/api/v1/movies",), None)
    rec.request_id = "rid-1"
    rec.status = 200
    out = json.loads(JsonFormatter().format(rec))
    assert out["message"] == "GET /api/v1/movies" and out["request_id"] == "rid-1" and out["status"] == 200


def test_sentry_events_are_scrubbed():
    token = request_id_var.set("rid-xyz")
    try:
        event = scrub_event(
            {
                "request": {
                    "headers": {"Authorization": "Bearer abc", "Cookie": "refresh=1", "User-Agent": "x"},
                    "cookies": {"refresh": "1"},
                    "data": {"password": "hunter2"},
                    "query_string": "token=abc",
                }
            }
        )
    finally:
        request_id_var.reset(token)
    req = event["request"]
    assert req["headers"]["Authorization"] == "[removed]" and req["headers"]["Cookie"] == "[removed]"
    assert "cookies" not in req and "data" not in req and "query_string" not in req
    assert event["tags"]["request_id"] == "rid-xyz"
