"""movie metadata, user phone, email nullable

Revision ID: b7c2a1d3e4f5
Revises: a5b1c0de0001
Create Date: 2026-11-06 12:25:02.397440

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c2a1d3e4f5'
down_revision: Union[str, Sequence[str], None] = 'a5b1c0de0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('movies', sa.Column('poster_url', sa.String(length=500), nullable=True))
    op.add_column('movies', sa.Column('genre', sa.String(length=100), server_default='', nullable=False))
    op.add_column('movies', sa.Column('language', sa.String(length=50), server_default='English', nullable=False))
    op.add_column('movies', sa.Column('rating', sa.Numeric(precision=3, scale=1), nullable=True))
    op.add_column('users', sa.Column('phone', sa.String(length=20), nullable=True))
    op.create_index(op.f('ix_users_phone'), 'users', ['phone'], unique=True)
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE email IS NULL")  # phone-only users can't exist without a nullable email
    op.alter_column('users', 'email', existing_type=sa.String(length=255), nullable=False)
    op.drop_index(op.f('ix_users_phone'), table_name='users')
    op.drop_column('users', 'phone')
    op.drop_column('movies', 'rating')
    op.drop_column('movies', 'language')
    op.drop_column('movies', 'genre')
    op.drop_column('movies', 'poster_url')
