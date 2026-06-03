"""ensure user registration date

Revision ID: 7a9c2e4f6b8d
Revises: 6e8f0a2b4c5d
Create Date: 2026-06-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a9c2e4f6b8d"
down_revision: Union[str, Sequence[str], None] = "6e8f0a2b4c5d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    if not _table_exists("users"):
        return

    bind = op.get_bind()
    if not _column_exists("users", "created_at"):
        op.add_column("users", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))

    if bind.dialect.name == "postgresql":
        op.execute("UPDATE users SET created_at = now() WHERE created_at IS NULL")
        op.alter_column(
            "users",
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
    else:
        op.execute("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")


def downgrade() -> None:
    if not _table_exists("users") or not _column_exists("users", "created_at"):
        return

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "users",
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            server_default=None,
            nullable=True,
        )
