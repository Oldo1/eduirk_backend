"""add user full name

Revision ID: 9c2a1d7e5f3b
Revises: 0f9a2b3c4d5e
Create Date: 2026-05-14 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9c2a1d7e5f3b"
down_revision: Union[str, None] = "0f9a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "full_name")
