"""Database engine and session handling."""
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


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
