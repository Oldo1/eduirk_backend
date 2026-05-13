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


def _ensure_auth_tables(inspector: sa.Inspector) -> None:
    tables = set(inspector.get_table_names())

    if "user_role" not in tables:
        op.create_table(
            "user_role",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("role_name", sa.String(length=50), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("role_name"),
        )
        op.create_index(op.f("ix_user_role_id"), "user_role", ["id"], unique=False)
        tables.add("user_role")

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("password_hash", sa.String(), nullable=False),
            sa.Column("username", sa.String(length=100), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("role_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["role_id"], ["user_role.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
        )
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
        op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
        op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _ensure_auth_tables(inspector)
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
    if "user_role" in inspector.get_table_names():
        op.execute("DELETE FROM user_role WHERE role_name = 'domu_editor'")
