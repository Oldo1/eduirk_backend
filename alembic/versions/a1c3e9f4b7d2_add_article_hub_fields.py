"""add article hub fields

Revision ID: a1c3e9f4b7d2
Revises: f6b8c1d2e3a4
Create Date: 2026-04-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c3e9f4b7d2"
down_revision: Union[str, Sequence[str], None] = "f6b8c1d2e3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    if not _has_column(inspector, "article", "hub_kind"):
        op.add_column("article", sa.Column("hub_kind", sa.String(length=64), nullable=True))
    if not _has_column(inspector, "article", "hub_path"):
        op.add_column("article", sa.Column("hub_path", sa.String(length=160), nullable=True))

    try:
        op.create_index(op.f("ix_article_hub_kind"), "article", ["hub_kind"], unique=False)
    except Exception:
        pass
    try:
        op.create_index(op.f("ix_article_hub_path"), "article", ["hub_path"], unique=False)
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    try:
        op.drop_index(op.f("ix_article_hub_path"), table_name="article")
    except Exception:
        pass
    try:
        op.drop_index(op.f("ix_article_hub_kind"), table_name="article")
    except Exception:
        pass

    if _has_column(inspector, "article", "hub_path"):
        op.drop_column("article", "hub_path")
    if _has_column(inspector, "article", "hub_kind"):
        op.drop_column("article", "hub_kind")
