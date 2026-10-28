"""App settings, read from environment variables or a .env file."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ticketdb"
    jwt_secret: str = "dev-only-secret-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    db_pool_size: int = 20
    redis_url: str = "redis://localhost:6379/0"
    hold_ttl_seconds: int = 300

    rate_limit_window_seconds: int = 60
    rate_limit_login_per_window: int = 10
    rate_limit_hold_per_window: int = 30
    rate_limit_booking_per_window: int = 10

    payment_due_minutes: int = 10
    payment_sweep_interval_seconds: int = 30


settings = Settings()
