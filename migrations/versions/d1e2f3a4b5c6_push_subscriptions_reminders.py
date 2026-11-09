"""web push subscriptions and sent reminders

Revision ID: d1e2f3a4b5c6
Revises: c9d4e5f6a7b8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c9d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('push_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('endpoint', sa.String(length=1000), nullable=False),
        sa.Column('p256dh', sa.String(length=200), nullable=False),
        sa.Column('auth', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint'),
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'])
    op.create_table('reminders_sent',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_id'),
    )


def downgrade() -> None:
    op.drop_table('reminders_sent')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
