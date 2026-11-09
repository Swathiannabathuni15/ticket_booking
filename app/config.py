"""App settings, read from environment variables or a .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ticketdb"
    jwt_secret: str = "dev-only-secret-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    # Stay signed in: 30 days, renewed every time the site is opened (POST /auth/refresh),
    # so people who use the site are not asked to sign in again.
    jwt_expire_minutes: int = 60 * 24 * 30
    db_pool_size: int = 20
    # How long a request waits for a free DB connection before we shed it as a 503.
    db_pool_timeout_seconds: int = 5
    redis_url: str = "redis://localhost:6379/0"
    hold_ttl_seconds: int = 300
    # Cumulative cap on one user's held seats on one showtime, across
    # however many separate /holds calls it took - not the same as the
    # per-request limit (MAX_SEATS_PER_BOOKING in app/schemas.py), which
    # only bounds a single call.
    max_held_seats_per_showtime: int = 8

    rate_limit_window_seconds: int = 60
    rate_limit_login_per_window: int = 10
    rate_limit_hold_per_window: int = 30
    rate_limit_booking_per_window: int = 10

    payment_due_minutes: int = 10
    payment_sweep_interval_seconds: int = 30

    # Browser origins allowed to call the API (comma separated). Vite dev server by default.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Any port on localhost is also allowed (Vite moves to 5174, 5175... if 5173 is busy).
    # For a real deployment set this to "" and list the real website in CORS_ORIGINS.
    cors_origin_regex: str = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"

    # Booking assistant (optional). No key => POST /agent/chat answers 503.
    anthropic_api_key: str = ""
    agent_model: str = "claude-haiku-5-5"
    rate_limit_agent_per_window: int = 10

    # Mobile OTP sign-in. No SMS provider is wired up, so demo mode returns the
    # code in the API response. MUST be False in any real deployment.
    otp_dev_mode: bool = True
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5

    # Rate limits are per IDENTITY (email, phone, or signed-in user id), not per IP address.
    rate_limit_user_per_second: int = 20      # the "gateway": any signed-in user, all endpoints
    rate_limit_payment_per_window: int = 10

    # Simulated bank + its signed webhooks (see app/services/fake_gateway.py)
    payment_webhook_secret: str = "dev-webhook-secret-change-me-0123456789"
    payment_webhook_url: str = "http://127.0.0.1:8000/webhooks/payments"
    payment_webhook_tolerance_seconds: int = 300
    gateway_webhook_delay_seconds: float = 2.0
    gateway_always_send_webhook: bool = False
    # Lets the client pick tok_ok / tok_decline / tok_timeout to demo each outcome.
    gateway_allow_test_methods: bool = True
    payment_reconcile_after_seconds: int = 60
    payment_give_up_after_seconds: int = 900

    # Web Push (phone / desktop notifications). Keys are generated automatically
    # into .vapid_keys.json on first use; set these to use your own.
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    reminder_minutes: int = 15

    # Split-the-bill share links
    split_link_ttl_hours: int = 48
    split_max_shares: int = 20
    rate_limit_share_pay_per_window: int = 20


settings = Settings()
