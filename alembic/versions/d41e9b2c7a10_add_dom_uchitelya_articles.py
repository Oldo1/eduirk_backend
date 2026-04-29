"""add dom uchitelya articles

Revision ID: d41e9b2c7a10
Revises: c2e8b9f0a1d4
Create Date: 2026-04-29 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d41e9b2c7a10"
down_revision: Union[str, Sequence[str], None] = "c2e8b9f0a1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        op.create_table(
            "article",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("excerpt", sa.String(length=800), nullable=True),
            sa.Column("image", sa.String(length=500), nullable=True),
            sa.Column("blocks", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("categories", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("publishing_scope", sa.String(length=20), nullable=False, server_default="both"),
            sa.Column("author_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("status IN ('draft', 'published', 'archive')", name="article_status_chk"),
            sa.CheckConstraint(
                "publishing_scope IN ('imcro_only', 'dom_uchitelya_only', 'both')",
                name="article_publishing_scope_chk",
            ),
            sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_article_id"), "article", ["id"], unique=False)
        op.create_index(op.f("ix_article_slug"), "article", ["slug"], unique=True)
        op.create_index(op.f("ix_article_status"), "article", ["status"], unique=False)
        op.create_index(op.f("ix_article_publishing_scope"), "article", ["publishing_scope"], unique=False)

    if bind.dialect.name == "postgresql":
        op.execute("INSERT INTO user_role (role_name) VALUES ('domu_editor') ON CONFLICT (role_name) DO NOTHING")
    else:
        exists = bind.execute(sa.text("SELECT id FROM user_role WHERE role_name = :role"), {"role": "domu_editor"}).first()
        if not exists:
            bind.execute(sa.text("INSERT INTO user_role (role_name) VALUES (:role)"), {"role": "domu_editor"})


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "article" in inspector.get_table_names():
        op.drop_index(op.f("ix_article_publishing_scope"), table_name="article")
        op.drop_index(op.f("ix_article_status"), table_name="article")
        op.drop_index(op.f("ix_article_slug"), table_name="article")
        op.drop_index(op.f("ix_article_id"), table_name="article")
        op.drop_table("article")
    op.execute("DELETE FROM user_role WHERE role_name = 'domu_editor'")
