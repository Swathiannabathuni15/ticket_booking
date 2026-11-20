"""App settings, read from environment variables or a .env file."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" turns on startup safety checks (see production_problems below).
    environment: str = "development"
    # off = run the worker and scheduler yourself; inprocess = the web server runs the
    # cleanup / reminder loop itself (one process: fits free hosting).
    background_jobs: str = "off"
    # Logs: "text" (easy to read in a terminal) or "json" (one line per event, for a log service).
    # Empty = json in production, text otherwise.
    log_format: str = ""
    log_level: str = "INFO"
    # Error tracking: set SENTRY_DSN (free tier at sentry.io) to get crashes with their request id.
    sentry_dsn: str = ""

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ticketdb"
    jwt_secret: str = "dev-only-secret-change-me-in-production-0123456789"  # noqa: S105 (dev default; production refuses it)
    jwt_algorithm: str = "HS256"
    # Stay signed in: 30 days, renewed every time the site is opened (POST /auth/refresh),
    # so people who use the site are not asked to sign in again.
    jwt_expire_minutes: int = 60 * 24 * 30
    # A session can be renewed again and again, but never beyond this many days from the original sign-in.
    max_session_days: int = 90
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
    # Any port on localhost AND on private network addresses (192.168.x.x, 10.x.x.x,
    # 172.16-31.x.x) is also allowed, so a phone on the same Wi-Fi can use the site in
    # development. For a real deployment set this to "" and list the real website in CORS_ORIGINS.
    cors_origin_regex: str = (
        r"^http://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$"
    )

    # Booking assistant (optional). No key at all => POST /agent/chat answers 503.
    anthropic_api_key: str = ""
    # Google Gemini works too. "auto" uses Gemini if GEMINI_API_KEY is set, else Anthropic.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    agent_provider: str = "auto"  # auto | gemini | anthropic
    agent_model: str = "claude-haiku-5-5"
    rate_limit_agent_per_window: int = 10

    # Mobile OTP sign-in. No SMS provider is wired up, so demo mode returns the
    # code in the API response. MUST be False in any real deployment.
    otp_dev_mode: bool = True
    # How codes are really delivered. With nothing configured (and otp_dev_mode on) the
    # code is shown on screen - demo only. See app/services/otp_delivery.py.
    otp_sms_provider: str = "none"  # none | twilio | msg91
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""  # a Twilio number like +1555..., or a Messaging Service SID (MG...)
    msg91_auth_key: str = ""
    msg91_template_id: str = ""  # an approved OTP template (India requires DLT approval)
    smtp_host: str = ""  # e.g. smtp.gmail.com (use an "app password", not your real password)
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    otp_brand: str = "Ticketshow"
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5

    # Rate limits are per IDENTITY (email, phone, or signed-in user id), not per IP address.
    rate_limit_user_per_second: int = 20  # the "gateway": any signed-in user, all endpoints
    rate_limit_payment_per_window: int = 10

    # Who takes the money: "simulated" (the fake bank below: no keys, no real anything) or "razorpay"
    # (see app/services/razorpay_gateway.py). With Razorpay, the fake card endpoint is switched OFF.
    payment_provider: str = "simulated"
    razorpay_key_id: str = ""  # public id, e.g. rzp_test_xxxx (test mode: no real money moves)
    razorpay_key_secret: str = ""  # secret: .env only, never committed
    razorpay_webhook_secret: str = ""  # the secret you type when adding the webhook in the Razorpay dashboard
    razorpay_api_base: str = "https://api.razorpay.com/v1"
    razorpay_checkout_name: str = "Ticketshow"  # shown at the top of the Razorpay window

    # Simulated bank + its signed webhooks (see app/services/fake_gateway.py)
    payment_webhook_secret: str = "dev-webhook-secret-change-me-0123456789"  # noqa: S105 (dev default; production refuses it)
    payment_webhook_url: str = "http://127.0.0.1:8000/api/v1/webhooks/payments"
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
    # "Morning / evening" in the seat finder means the clock where the cinemas are.
    display_timezone: str = "Asia/Kolkata"
    # > 0: the server's background loop keeps showtimes created this many days ahead (0 = off;
    # you can also run scripts/schedule_showtimes.py yourself, e.g. once a day).
    auto_schedule_days: int = 0

    # The website's public address. The QR code on a ticket points at <this>/ticket/<code>.
    public_base_url: str = "http://localhost:5173"

    # Split-the-bill share links
    split_link_ttl_hours: int = 48
    split_max_shares: int = 20
    rate_limit_share_pay_per_window: int = 20

    @field_validator("database_url")
    @classmethod
    def _sqlalchemy_url(cls, v: str) -> str:
        """Hosts hand out postgres:// or postgresql:// URLs; we use the psycopg 3 driver."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    def production_problems(self) -> list[str]:
        """Things that must NOT be true on a public server. Empty list = fine."""
        problems = []
        if len(self.jwt_secret) < 32 or self.jwt_secret.startswith("dev-") or "change-me" in self.jwt_secret:
            problems.append("JWT_SECRET is a development value (anyone could forge logins). Set a long random one.")
        if "change-me" in self.payment_webhook_secret or self.payment_webhook_secret.startswith("dev-"):
            problems.append("PAYMENT_WEBHOOK_SECRET is a development value. Set a long random one.")
        if self.payment_provider not in ("simulated", "razorpay"):
            problems.append("PAYMENT_PROVIDER must be 'razorpay' or 'simulated'.")
        elif self.payment_provider == "simulated":
            problems.append(
                "PAYMENT_PROVIDER is 'simulated': the fake bank approves every payment, so tickets would be free. "
                "Set PAYMENT_PROVIDER=razorpay (test keys are fine for a demo)."
            )
        elif not (self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_webhook_secret):
            problems.append(
                "PAYMENT_PROVIDER=razorpay needs RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET."
            )
        if self.gateway_allow_test_methods:
            problems.append(
                "GATEWAY_ALLOW_TEST_METHODS is on: the browser chooses which test 'card' works. "
                "Set GATEWAY_ALLOW_TEST_METHODS=false (required before connecting a real bank)."
            )
        if self.otp_dev_mode:
            problems.append(
                "OTP_DEV_MODE is on: sign-in codes would be shown on screen to anyone. Set OTP_DEV_MODE=false."
            )
        if self.cors_origin_regex and "localhost" in self.cors_origin_regex:
            problems.append(
                "CORS_ORIGIN_REGEX still allows localhost and home networks. Set CORS_ORIGIN_REGEX= (empty) and list your website in CORS_ORIGINS."
            )
        if not self.public_base_url.startswith("https://"):
            problems.append(
                "PUBLIC_BASE_URL is not an https:// address, so the QR codes on tickets would point nowhere useful. "
                "Set it to your website, e.g. PUBLIC_BASE_URL=https://your-website.example.com"
            )
        return problems


settings = Settings()
