"""add article sections

Revision ID: 3f2a8c9d1e6b
Revises: 2c4d6e8f0a12
Create Date: 2026-05-26 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3f2a8c9d1e6b"
down_revision: Union[str, Sequence[str], None] = "2c4d6e8f0a12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" in inspector.get_table_names() and not _has_column(inspector, "article", "sections"):
        op.add_column("article", sa.Column("sections", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" in inspector.get_table_names() and _has_column(inspector, "article", "sections"):
        op.drop_column("article", "sections")
