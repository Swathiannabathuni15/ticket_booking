"""pricing rules (weekend / evening prices) and the note on each showtime

Revision ID: a7b8c9d0e1f2
Revises: f3a4b5c6d7e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("days", sa.JSON(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=True),
        sa.Column("end_minute", sa.Integer(), nullable=True),
        sa.Column("seat_type", sa.String(length=30), nullable=True),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("showtimes", sa.Column("price_note", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("showtimes", "price_note")
    op.drop_table("pricing_rules")
