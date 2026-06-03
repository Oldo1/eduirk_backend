"""remove username columns

Revision ID: 6e8f0a2b4c5d
Revises: 5d7e9a1b3c4f
Create Date: 2026-06-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6e8f0a2b4c5d"
down_revision: Union[str, Sequence[str], None] = "5d7e9a1b3c4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USERNAME_TABLES = (
    "users",
    "appointments",
    "tpmpk_appointment",
    "assistant_chat_session",
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"]
        for column in _inspector().get_columns(table_name)
    }


def _index_exists(table_name: str, index_name: str) -> bool:
    return index_name in {
        index["name"]
        for index in _inspector().get_indexes(table_name)
    }


def upgrade() -> None:
    if _table_exists("users") and _index_exists("users", "ix_users_username"):
        op.drop_index("ix_users_username", table_name="users")

    for table_name in USERNAME_TABLES:
        if _table_exists(table_name) and _column_exists(table_name, "username"):
            op.drop_column(table_name, "username")


def downgrade() -> None:
    for table_name in USERNAME_TABLES:
        if _table_exists(table_name) and not _column_exists(table_name, "username"):
            op.add_column(
                table_name,
                sa.Column("username", sa.String(length=100), nullable=True),
            )

    if _table_exists("users") and not _index_exists("users", "ix_users_username"):
        op.create_index("ix_users_username", "users", ["username"], unique=True)
