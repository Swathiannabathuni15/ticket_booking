"""payment provider and Razorpay order id on payment attempts

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_attempts", sa.Column("provider", sa.String(length=20), server_default="simulated", nullable=False)
    )
    op.add_column("payment_attempts", sa.Column("gateway_order_id", sa.String(length=60), nullable=True))
    op.create_unique_constraint("uq_payment_attempts_gateway_order_id", "payment_attempts", ["gateway_order_id"])


def downgrade() -> None:
    op.drop_constraint("uq_payment_attempts_gateway_order_id", "payment_attempts", type_="unique")
    op.drop_column("payment_attempts", "gateway_order_id")
    op.drop_column("payment_attempts", "provider")
