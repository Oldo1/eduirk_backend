"""add user fio fields

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


USER_FIO_COLUMNS = (
    ("last_name", sa.String(length=100)),
    ("first_name", sa.String(length=100)),
    ("middle_name", sa.String(length=100)),
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {
        column["name"]
        for column in inspector.get_columns(table_name)
    }


def upgrade() -> None:
    inspector = _inspector()
    if not _has_table(inspector, "users"):
        return

    for column_name, column_type in USER_FIO_COLUMNS:
        if not _has_column(inspector, "users", column_name):
            op.add_column("users", sa.Column(column_name, column_type, nullable=True))


def downgrade() -> None:
    inspector = _inspector()
    if not _has_table(inspector, "users"):
        return

    for column_name, _column_type in reversed(USER_FIO_COLUMNS):
        if _has_column(inspector, "users", column_name):
            op.drop_column("users", column_name)
