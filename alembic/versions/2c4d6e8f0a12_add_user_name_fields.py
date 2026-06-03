"""add user name fields

Revision ID: 2c4d6e8f0a12
Revises: 1a2b3c4d5e6f
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa


revision = "2c4d6e8f0a12"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    return any(column["name"] == column_name for column in sa.inspect(bind).get_columns(table_name))


def upgrade() -> None:
    if not _table_exists("users"):
        return

    for column in ("last_name", "first_name", "middle_name"):
        if not _column_exists("users", column):
            op.add_column("users", sa.Column(column, sa.String(length=100), nullable=True))


def downgrade() -> None:
    if not _table_exists("users"):
        return

    for column in ("middle_name", "first_name", "last_name"):
        if _column_exists("users", column):
            op.drop_column("users", column)
