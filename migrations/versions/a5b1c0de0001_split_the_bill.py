"""split the bill: bill_splits, split_shares, ledger_entries

Revision ID: a5b1c0de0001
Revises: 028f68caa1f1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a5b1c0de0001'
down_revision: Union[str, Sequence[str], None] = '028f68caa1f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('bill_splits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=False),
        sa.Column('total_cents', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_bill_splits_booking_id'), 'bill_splits', ['booking_id'], unique=True)
    op.create_table('split_shares',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('split_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('is_owner', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('nonce', sa.String(length=32), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['split_id'], ['bill_splits.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_split_shares_split_id'), 'split_shares', ['split_id'], unique=False)
    op.create_table('ledger_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('share_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['share_id'], ['split_shares.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('share_id', 'kind', name='uq_ledger_share_kind'),
    )
    op.create_index(op.f('ix_ledger_entries_share_id'), 'ledger_entries', ['share_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ledger_entries')
    op.drop_table('split_shares')
    op.drop_table('bill_splits')
