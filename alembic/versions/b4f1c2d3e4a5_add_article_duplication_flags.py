"""add article duplication flags

Revision ID: b4f1c2d3e4a5
Revises: a1c3e9f4b7d2
Create Date: 2026-04-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4f1c2d3e4a5"
down_revision: Union[str, Sequence[str], None] = "a1c3e9f4b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    if not _has_column(inspector, "article", "duplicate_to_main"):
        op.add_column("article", sa.Column("duplicate_to_main", sa.Boolean(), nullable=False, server_default=sa.false()))
    if not _has_column(inspector, "article", "duplicate_to_events"):
        op.add_column("article", sa.Column("duplicate_to_events", sa.Boolean(), nullable=False, server_default=sa.false()))

    try:
        op.create_index(op.f("ix_article_duplicate_to_main"), "article", ["duplicate_to_main"], unique=False)
    except Exception:
        pass
    try:
        op.create_index(op.f("ix_article_duplicate_to_events"), "article", ["duplicate_to_events"], unique=False)
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    try:
        op.drop_index(op.f("ix_article_duplicate_to_events"), table_name="article")
    except Exception:
        pass
    try:
        op.drop_index(op.f("ix_article_duplicate_to_main"), table_name="article")
    except Exception:
        pass

    if _has_column(inspector, "article", "duplicate_to_events"):
        op.drop_column("article", "duplicate_to_events")
    if _has_column(inspector, "article", "duplicate_to_main"):
        op.drop_column("article", "duplicate_to_main")
