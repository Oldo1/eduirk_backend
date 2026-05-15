"""extend articles editor fields

Revision ID: e8f7a4c2d9b1
Revises: d41e9b2c7a10
Create Date: 2026-04-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f7a4c2d9b1"
down_revision: Union[str, Sequence[str], None] = "d41e9b2c7a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "users" in inspector.get_table_names() and not _has_column(inspector, "users", "allowed_methodika_subjects"):
        op.add_column("users", sa.Column("allowed_methodika_subjects", sa.JSON(), nullable=False, server_default="[]"))

    if "article" not in inspector.get_table_names():
        return

    columns = {
        "lead": sa.Column("lead", sa.String(length=800), nullable=True),
        "body": sa.Column("body", sa.String(), nullable=False, server_default=""),
        "cover_image_url": sa.Column("cover_image_url", sa.String(length=500), nullable=True),
        "is_pinned": sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        "methodika_subject": sa.Column("methodika_subject", sa.String(length=120), nullable=True),
        "dom_uchitelya_section": sa.Column("dom_uchitelya_section", sa.String(length=120), nullable=True),
        "noko_section": sa.Column("noko_section", sa.String(length=120), nullable=True),
    }
    for name, column in columns.items():
        if not _has_column(inspector, "article", name):
            op.add_column("article", column)

    bind.execute(sa.text("UPDATE article SET lead = excerpt WHERE lead IS NULL AND excerpt IS NOT NULL"))
    bind.execute(sa.text("UPDATE article SET cover_image_url = image WHERE cover_image_url IS NULL AND image IS NOT NULL"))

    for column_name in ("is_pinned", "methodika_subject", "dom_uchitelya_section", "noko_section"):
        index_name = op.f(f"ix_article_{column_name}")
        try:
            op.create_index(index_name, "article", [column_name], unique=False)
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "article" in inspector.get_table_names():
        for column_name in ("noko_section", "dom_uchitelya_section", "methodika_subject", "is_pinned"):
            try:
                op.drop_index(op.f(f"ix_article_{column_name}"), table_name="article")
            except Exception:
                pass
        for column_name in (
            "noko_section",
            "dom_uchitelya_section",
            "methodika_subject",
            "is_pinned",
            "cover_image_url",
            "body",
            "lead",
        ):
            if _has_column(inspector, "article", column_name):
                op.drop_column("article", column_name)

    if "users" in inspector.get_table_names() and _has_column(inspector, "users", "allowed_methodika_subjects"):
        op.drop_column("users", "allowed_methodika_subjects")
