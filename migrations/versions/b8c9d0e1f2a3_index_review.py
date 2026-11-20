"""index review: showtime time indexes, partial indexes for the sweepers, drop a redundant seat index

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2

On a large live database, create these with CREATE INDEX CONCURRENTLY (outside a transaction) instead,
so the tables are not locked while the index builds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # showtimes: (movie, time) and (screen, time) replace the single-column ones; plus time alone.
    op.create_index("ix_showtimes_movie_starts", "showtimes", ["movie_id", "starts_at"])
    op.create_index("ix_showtimes_screen_starts", "showtimes", ["screen_id", "starts_at"])
    op.create_index("ix_showtimes_starts_at", "showtimes", ["starts_at"])
    op.drop_index("ix_showtimes_movie_id", table_name="showtimes")
    op.drop_index("ix_showtimes_screen_id", table_name="showtimes")
    # showtime_seats: the unique (showtime_id, row_label, seat_number) index already covers showtime_id.
    op.drop_index("ix_showtime_seats_showtime_id", table_name="showtime_seats")
    # Partial indexes for the background jobs and the dashboard.
    op.create_index(
        "ix_bookings_unpaid_due", "bookings", ["payment_due_at"], postgresql_where=sa.text("status = 'pending_payment'")
    )
    op.create_index("ix_bookings_paid_at", "bookings", ["paid_at"], postgresql_where=sa.text("status = 'confirmed'"))
    op.create_index(
        "ix_attempts_pending_created", "payment_attempts", ["created_at"], postgresql_where=sa.text("status = 'pending'")
    )


def downgrade() -> None:
    op.drop_index("ix_attempts_pending_created", table_name="payment_attempts")
    op.drop_index("ix_bookings_paid_at", table_name="bookings")
    op.drop_index("ix_bookings_unpaid_due", table_name="bookings")
    op.create_index("ix_showtime_seats_showtime_id", "showtime_seats", ["showtime_id"])
    op.create_index("ix_showtimes_screen_id", "showtimes", ["screen_id"])
    op.create_index("ix_showtimes_movie_id", "showtimes", ["movie_id"])
    op.drop_index("ix_showtimes_starts_at", table_name="showtimes")
    op.drop_index("ix_showtimes_screen_starts", table_name="showtimes")
    op.drop_index("ix_showtimes_movie_starts", table_name="showtimes")
