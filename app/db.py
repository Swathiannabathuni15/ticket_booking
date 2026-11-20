"""Database engine and session handling."""

from typing import TypeVar

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# pool_pre_ping drops dead connections instead of crashing on them.
engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=0,
    pool_pre_ping=True,
    pool_timeout=settings.db_pool_timeout_seconds,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


T = TypeVar("T")


class MissingRow(LookupError):
    """A row that must exist (a foreign key points at it) was not found."""


def required(value: T | None, what: str = "row") -> T:
    """`db.get(...)` and `db.scalar(...)` may return None. Use this where the row MUST exist
    (we hold its id from a foreign key or just locked it): a clear error beats an AttributeError."""
    if value is None:
        raise MissingRow(f"{what} not found")
    return value


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
