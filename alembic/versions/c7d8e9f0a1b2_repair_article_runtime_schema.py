"""repair article runtime schema

Revision ID: c7d8e9f0a1b2
Revises: b4f1c2d3e4a5
Create Date: 2026-06-28 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b4f1c2d3e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def _has_index_in_schema(inspector: sa.Inspector, index_name: str) -> bool:
    for table_name in inspector.get_table_names():
        if _has_index(inspector, table_name, index_name):
            return True
    return False


def _has_fk(
    inspector: sa.Inspector,
    table_name: str,
    constrained_columns: tuple[str, ...],
    referred_table: str,
) -> bool:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if tuple(foreign_key.get("constrained_columns") or ()) != constrained_columns:
            continue
        if foreign_key.get("referred_table") == referred_table:
            return True
    return False


def _create_index_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: list[str],
    unique: bool = False,
) -> None:
    if not _has_index(inspector, table_name, index_name) and not _has_index_in_schema(inspector, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _ensure_article_lookup_tables(inspector: sa.Inspector) -> None:
    tables = set(inspector.get_table_names())

    if "article_status" not in tables:
        op.create_table(
            "article_status",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=50), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        tables.add("article_status")

    if "category" not in tables:
        op.create_table(
            "category",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("slug", sa.String(length=200), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        tables.add("category")

    if "tag" not in tables:
        op.create_table(
            "tag",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("slug", sa.String(length=200), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def _create_article_table() -> None:
    op.create_table(
        "article",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("slug", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("excerpt", sa.String(length=800), nullable=True),
        sa.Column("image", sa.String(length=500), nullable=True),
        sa.Column("lead", sa.String(length=800), nullable=True),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("cover_image_url", sa.String(length=500), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duplicate_to_main", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duplicate_to_events", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("attachments", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("categories", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("publishing_scope", sa.String(length=20), nullable=False, server_default="both"),
        sa.Column("methodika_subject", sa.String(length=120), nullable=True),
        sa.Column("dom_uchitelya_section", sa.String(length=120), nullable=True),
        sa.Column("noko_section", sa.String(length=120), nullable=True),
        sa.Column("hub_kind", sa.String(length=64), nullable=True),
        sa.Column("hub_path", sa.String(length=160), nullable=True),
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
        sa.ForeignKeyConstraint(["status_id"], ["article_status.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def _ensure_article_columns(inspector: sa.Inspector) -> None:
    if "article" not in inspector.get_table_names():
        _create_article_table()
        return

    columns = {
        "title": sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        "slug": sa.Column("slug", sa.String(length=500), nullable=False, server_default=""),
        "content": sa.Column("content", sa.Text(), nullable=True),
        "status_id": sa.Column("status_id", sa.Integer(), nullable=True),
        "status": sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        "excerpt": sa.Column("excerpt", sa.String(length=800), nullable=True),
        "image": sa.Column("image", sa.String(length=500), nullable=True),
        "lead": sa.Column("lead", sa.String(length=800), nullable=True),
        "body": sa.Column("body", sa.Text(), nullable=False, server_default=""),
        "cover_image_url": sa.Column("cover_image_url", sa.String(length=500), nullable=True),
        "is_pinned": sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        "duplicate_to_main": sa.Column("duplicate_to_main", sa.Boolean(), nullable=False, server_default=sa.false()),
        "duplicate_to_events": sa.Column("duplicate_to_events", sa.Boolean(), nullable=False, server_default=sa.false()),
        "blocks": sa.Column("blocks", sa.JSON(), nullable=False, server_default="[]"),
        "attachments": sa.Column("attachments", sa.JSON(), nullable=False, server_default="[]"),
        "categories": sa.Column("categories", sa.JSON(), nullable=False, server_default="[]"),
        "tags": sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        "publishing_scope": sa.Column("publishing_scope", sa.String(length=20), nullable=False, server_default="both"),
        "methodika_subject": sa.Column("methodika_subject", sa.String(length=120), nullable=True),
        "dom_uchitelya_section": sa.Column("dom_uchitelya_section", sa.String(length=120), nullable=True),
        "noko_section": sa.Column("noko_section", sa.String(length=120), nullable=True),
        "hub_kind": sa.Column("hub_kind", sa.String(length=64), nullable=True),
        "hub_path": sa.Column("hub_path", sa.String(length=160), nullable=True),
        "author_id": sa.Column("author_id", sa.Integer(), nullable=True),
        "created_at": sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        "published_at": sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    }
    for name, column in columns.items():
        if not _has_column(inspector, "article", name):
            op.add_column("article", column)


def _ensure_article_relations(inspector: sa.Inspector) -> None:
    tables = set(inspector.get_table_names())

    if "article_category" not in tables:
        op.create_table(
            "article_category",
            sa.Column("article_id", sa.Integer(), nullable=False),
            sa.Column("category_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
            sa.ForeignKeyConstraint(["category_id"], ["category.id"]),
            sa.PrimaryKeyConstraint("article_id", "category_id"),
        )

    if "article_tag" not in tables:
        op.create_table(
            "article_tag",
            sa.Column("article_id", sa.Integer(), nullable=False),
            sa.Column("tag_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["article_id"], ["article.id"]),
            sa.ForeignKeyConstraint(["tag_id"], ["tag.id"]),
            sa.PrimaryKeyConstraint("article_id", "tag_id"),
        )


def _ensure_article_indexes(bind) -> None:
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        return

    for name, columns in {
        op.f("ix_article_id"): ["id"],
        op.f("ix_article_slug"): ["slug"],
        op.f("ix_article_status"): ["status"],
        op.f("ix_article_status_ref_id"): ["status_id"],
        op.f("ix_article_publishing_scope"): ["publishing_scope"],
        op.f("ix_article_is_pinned"): ["is_pinned"],
        op.f("ix_article_duplicate_to_main"): ["duplicate_to_main"],
        op.f("ix_article_duplicate_to_events"): ["duplicate_to_events"],
        op.f("ix_article_methodika_subject"): ["methodika_subject"],
        op.f("ix_article_dom_uchitelya_section"): ["dom_uchitelya_section"],
        op.f("ix_article_noko_section"): ["noko_section"],
        op.f("ix_article_hub_kind"): ["hub_kind"],
        op.f("ix_article_hub_path"): ["hub_path"],
    }.items():
        _create_index_if_missing(inspector, "article", name, columns, unique=False)


def _widen_article_text_columns(bind) -> None:
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names():
        return
    if _has_column(inspector, "article", "title"):
        op.alter_column(
            "article",
            "title",
            type_=sa.String(length=500),
            existing_type=sa.String(length=300),
            existing_nullable=False,
        )
    if _has_column(inspector, "article", "slug"):
        op.alter_column(
            "article",
            "slug",
            type_=sa.String(length=500),
            existing_type=sa.String(length=160),
            existing_nullable=False,
        )


def _seed_article_statuses(bind) -> None:
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            INSERT INTO article_status (name)
            SELECT value
            FROM (VALUES ('published'), ('draft'), ('archive')) AS seed(value)
            WHERE NOT EXISTS (
                SELECT 1
                FROM article_status
                WHERE article_status.name = seed.value
            )
            """
        )
        return

    for name in ("published", "draft", "archive"):
        exists = bind.execute(sa.text("SELECT id FROM article_status WHERE name = :name"), {"name": name}).first()
        if not exists:
            bind.execute(sa.text("INSERT INTO article_status (name) VALUES (:name)"), {"name": name})


def _backfill_article_values(bind) -> None:
    if "article" not in sa.inspect(bind).get_table_names():
        return

    bind.execute(sa.text("UPDATE article SET status = 'draft' WHERE status IS NULL"))
    bind.execute(sa.text("UPDATE article SET body = '' WHERE body IS NULL"))
    bind.execute(sa.text("UPDATE article SET lead = excerpt WHERE lead IS NULL AND excerpt IS NOT NULL"))
    bind.execute(sa.text("UPDATE article SET cover_image_url = image WHERE cover_image_url IS NULL AND image IS NOT NULL"))
    bind.execute(sa.text("UPDATE article SET content = body WHERE content IS NULL AND body IS NOT NULL"))

    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("UPDATE article SET blocks = '[]'::json WHERE blocks IS NULL"))
        bind.execute(sa.text("UPDATE article SET attachments = '[]'::json WHERE attachments IS NULL"))
        bind.execute(sa.text("UPDATE article SET categories = '[]'::json WHERE categories IS NULL"))
        bind.execute(sa.text("UPDATE article SET tags = '[]'::json WHERE tags IS NULL"))
        bind.execute(
            sa.text(
                """
                UPDATE article
                SET status_id = article_status.id
                FROM article_status
                WHERE article.status_id IS NULL
                  AND article.status = article_status.name
                """
            )
        )
        bind.execute(
            sa.text(
                """
                UPDATE article
                SET status = article_status.name
                FROM article_status
                WHERE article.status_id = article_status.id
                  AND (article.status IS NULL OR article.status = 'draft')
                """
            )
        )
    else:
        bind.execute(sa.text("UPDATE article SET blocks = '[]' WHERE blocks IS NULL"))
        bind.execute(sa.text("UPDATE article SET attachments = '[]' WHERE attachments IS NULL"))
        bind.execute(sa.text("UPDATE article SET categories = '[]' WHERE categories IS NULL"))
        bind.execute(sa.text("UPDATE article SET tags = '[]' WHERE tags IS NULL"))


def _ensure_status_fk(bind) -> None:
    inspector = sa.inspect(bind)
    if "article" not in inspector.get_table_names() or "article_status" not in inspector.get_table_names():
        return
    if not _has_column(inspector, "article", "status_id"):
        return
    if not _has_fk(inspector, "article", ("status_id",), "article_status"):
        op.create_foreign_key(
            op.f("article_status_id_fkey"),
            "article",
            "article_status",
            ["status_id"],
            ["id"],
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _ensure_article_lookup_tables(inspector)
    inspector = sa.inspect(bind)
    _ensure_article_columns(inspector)
    inspector = sa.inspect(bind)
    _ensure_article_relations(inspector)
    _ensure_article_indexes(bind)
    _widen_article_text_columns(bind)
    _seed_article_statuses(bind)
    _backfill_article_values(bind)
    _ensure_status_fk(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "article" in inspector.get_table_names():
        for index_name in (
            op.f("ix_article_hub_path"),
            op.f("ix_article_hub_kind"),
            op.f("ix_article_noko_section"),
            op.f("ix_article_dom_uchitelya_section"),
            op.f("ix_article_methodika_subject"),
            op.f("ix_article_duplicate_to_events"),
            op.f("ix_article_duplicate_to_main"),
            op.f("ix_article_is_pinned"),
            op.f("ix_article_status_ref_id"),
        ):
            if _has_index(inspector, "article", index_name):
                op.drop_index(index_name, table_name="article")

    for table_name in ("article_tag", "article_category"):
        if table_name in inspector.get_table_names():
            op.drop_table(table_name)
