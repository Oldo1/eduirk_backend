"""add article attachments

Revision ID: f6b8c1d2e3a4
Revises: e8f7a4c2d9b1
Create Date: 2026-04-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b8c1d2e3a4"
down_revision: Union[str, Sequence[str], None] = "e8f7a4c2d9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" in inspector.get_table_names() and not _has_column(inspector, "article", "attachments"):
        op.add_column("article", sa.Column("attachments", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" in inspector.get_table_names() and _has_column(inspector, "article", "attachments"):
        op.drop_column("article", "attachments")
