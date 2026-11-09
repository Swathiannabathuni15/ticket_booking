"""payment attempts, simulated gateway charges, webhook events

Revision ID: c9d4e5f6a7b8
Revises: b7c2a1d3e4f5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b7c2a1d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('payment_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=100), nullable=False),
        sa.Column('gateway_key', sa.String(length=140), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('charge_id', sa.String(length=60), nullable=True),
        sa.Column('failure_reason', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('gateway_key'),
        sa.UniqueConstraint('user_id', 'idempotency_key', name='uq_attempt_user_key'),
    )
    op.create_index(op.f('ix_payment_attempts_booking_id'), 'payment_attempts', ['booking_id'])
    op.create_index(op.f('ix_payment_attempts_user_id'), 'payment_attempts', ['user_id'])
    op.create_table('gateway_charges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('charge_id', sa.String(length=60), nullable=False),
        sa.Column('idempotency_key', sa.String(length=140), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('failure_reason', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('charge_id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_table('webhook_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.String(length=80), nullable=False),
        sa.Column('type', sa.String(length=60), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id'),
    )


def downgrade() -> None:
    op.drop_table('webhook_events')
    op.drop_table('gateway_charges')
    op.drop_index(op.f('ix_payment_attempts_user_id'), table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_booking_id'), table_name='payment_attempts')
    op.drop_table('payment_attempts')
