"""drop username fields

Revision ID: d9e0f1a2b3c4
Revises: c7d8e9f0a1b2
Create Date: 2026-06-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


USERNAME_TABLES = ("users", "assistant_chat_session")


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"]
        for column in inspector.get_columns(table_name)
    }


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {
        index["name"]
        for index in inspector.get_indexes(table_name)
    }


def upgrade() -> None:
    inspector = _inspector()

    if _has_table(inspector, "users") and _has_index(inspector, "users", "ix_users_username"):
        op.drop_index("ix_users_username", table_name="users")

    inspector = _inspector()
    for table_name in USERNAME_TABLES:
        if _has_table(inspector, table_name) and _has_column(inspector, table_name, "username"):
            op.drop_column(table_name, "username")


def downgrade() -> None:
    inspector = _inspector()

    for table_name in USERNAME_TABLES:
        if _has_table(inspector, table_name) and not _has_column(inspector, table_name, "username"):
            op.add_column(table_name, sa.Column("username", sa.String(length=100), nullable=True))

    inspector = _inspector()
    if _has_table(inspector, "users") and not _has_index(inspector, "users", "ix_users_username"):
        op.create_index("ix_users_username", "users", ["username"], unique=True)
