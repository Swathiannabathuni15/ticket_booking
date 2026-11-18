"""e-tickets: ticket code, check-in time, confirmation email time

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bookings', sa.Column('ticket_code', sa.String(length=16), nullable=True))
    op.add_column('bookings', sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bookings', sa.Column('confirmation_emailed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_bookings_ticket_code'), 'bookings', ['ticket_code'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_bookings_ticket_code'), table_name='bookings')
    op.drop_column('bookings', 'confirmation_emailed_at')
    op.drop_column('bookings', 'checked_in_at')
    op.drop_column('bookings', 'ticket_code')
