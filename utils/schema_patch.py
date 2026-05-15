"""
Добавление колонок для расширенной раскладки грамот (существующие БД без Alembic).
Поддерживается PostgreSQL; для SQLite можно расширить при необходимости.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def ensure_postgresql_extensions(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


def _pg_column_exists(conn, table: str, column: str) -> bool:
    r = conn.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).fetchone()
    return r is not None


def _sqlite_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


INTERNAL_DOCS_DEFAULT_ROLE_NAMES = {
    "admin",
    "administrator",
    "employee",
    "staff",
    "manager",
    "админ",
    "администратор",
    "сотрудник",
    "работник",
}


def _backfill_internal_docs_roles(conn) -> None:
    for role_name in INTERNAL_DOCS_DEFAULT_ROLE_NAMES:
        conn.execute(
            text(
                """
                UPDATE user_role
                SET can_access_internal_docs = TRUE
                WHERE can_access_internal_docs = FALSE
                  AND (role_name = :role_name OR lower(role_name) = :role_name)
                """
            ),
            {"role_name": role_name},
        )


def ensure_user_role_permission_columns(engine: Engine) -> None:
    dialect = engine.dialect.name

    if dialect == "postgresql":
        with engine.begin() as conn:
            table_exists = conn.execute(text("SELECT to_regclass('public.user_role')")).scalar()
            if not table_exists:
                return
            if not _pg_column_exists(conn, "user_role", "can_access_internal_docs"):
                conn.execute(
                    text(
                        "ALTER TABLE user_role "
                        "ADD COLUMN can_access_internal_docs BOOLEAN DEFAULT FALSE NOT NULL"
                    )
                )
            _backfill_internal_docs_roles(conn)
    elif dialect == "sqlite":
        with engine.begin() as conn:
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_role'")
            ).fetchall()
            if not tables:
                return
            if "can_access_internal_docs" not in _sqlite_columns(conn, "user_role"):
                conn.execute(
                    text(
                        "ALTER TABLE user_role "
                        "ADD COLUMN can_access_internal_docs BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            _backfill_internal_docs_roles(conn)


def ensure_certificate_layout_columns(engine: Engine) -> None:
    dialect = engine.dialect.name

    alters_pg: list[tuple[str, str, str]] = [
        ("users", "is_active", "BOOLEAN DEFAULT TRUE NOT NULL"),
        ("users", "allowed_methodika_subjects", "JSONB DEFAULT '[]'::jsonb NOT NULL"),
        ("certificate_templates", "signers_block_x_mm", "DOUBLE PRECISION DEFAULT 105"),
        ("certificate_templates", "signers_row_height_mm", "DOUBLE PRECISION DEFAULT 32"),
        ("certificate_templates", "signers_band_width_mm", "DOUBLE PRECISION DEFAULT 168"),
        ("certificate_templates", "signers_font_size", "DOUBLE PRECISION DEFAULT 10"),
        ("certificate_templates", "signers_text_color", "VARCHAR(16) DEFAULT '#1e293b'"),
        ("certificate_templates", "signers_font_weight", "VARCHAR(8) DEFAULT '400'"),
        ("certificate_templates", "signers_font_family", "VARCHAR(120) DEFAULT 'DejaVu'"),
        ("certificate_templates", "signers_position_color", "VARCHAR(16)"),
        ("certificate_templates", "signers_name_color", "VARCHAR(16)"),
        ("certificate_templates", "margin_left_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("certificate_templates", "margin_right_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("certificate_templates", "margin_top_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("certificate_templates", "margin_bottom_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("template_text_elements", "max_width_mm", "DOUBLE PRECISION"),
        ("template_text_elements", "max_height_mm", "DOUBLE PRECISION"),
        ("template_text_elements", "color", "VARCHAR(16) DEFAULT '#0F172A'"),
        ("template_text_elements", "font_weight", "VARCHAR(8) DEFAULT '400'"),
        ("template_text_elements", "font_family", "VARCHAR(120) DEFAULT 'DejaVu'"),
        ("template_signers", "offset_y_mm", "DOUBLE PRECISION DEFAULT 0"),
        ("template_signers", "facsimile_offset_x_mm", "DOUBLE PRECISION DEFAULT 0"),
        ("template_signers", "facsimile_offset_y_mm", "DOUBLE PRECISION DEFAULT 0"),
        ("template_signers", "facsimile_scale", "DOUBLE PRECISION DEFAULT 1"),
    ]

    alters_sqlite: list[tuple[str, str, str]] = [
        ("users", "is_active", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("users", "allowed_methodika_subjects", "TEXT DEFAULT '[]' NOT NULL"),
        ("certificate_templates", "signers_block_x_mm", "REAL DEFAULT 105"),
        ("certificate_templates", "signers_row_height_mm", "REAL DEFAULT 32"),
        ("certificate_templates", "signers_band_width_mm", "REAL DEFAULT 168"),
        ("certificate_templates", "signers_font_size", "REAL DEFAULT 10"),
        ("certificate_templates", "signers_text_color", "TEXT DEFAULT '#1e293b'"),
        ("certificate_templates", "signers_font_weight", "TEXT DEFAULT '400'"),
        ("certificate_templates", "signers_font_family", "TEXT DEFAULT 'DejaVu'"),
        ("certificate_templates", "signers_position_color", "TEXT"),
        ("certificate_templates", "signers_name_color", "TEXT"),
        ("certificate_templates", "margin_left_mm", "REAL DEFAULT 12"),
        ("certificate_templates", "margin_right_mm", "REAL DEFAULT 12"),
        ("certificate_templates", "margin_top_mm", "REAL DEFAULT 12"),
        ("certificate_templates", "margin_bottom_mm", "REAL DEFAULT 12"),
        ("template_text_elements", "max_width_mm", "REAL"),
        ("template_text_elements", "max_height_mm", "REAL"),
        ("template_text_elements", "color", "TEXT DEFAULT '#0F172A'"),
        ("template_text_elements", "font_weight", "TEXT DEFAULT '400'"),
        ("template_text_elements", "font_family", "TEXT DEFAULT 'DejaVu'"),
        ("template_signers", "offset_y_mm", "REAL DEFAULT 0"),
        ("template_signers", "facsimile_offset_x_mm", "REAL DEFAULT 0"),
        ("template_signers", "facsimile_offset_y_mm", "REAL DEFAULT 0"),
        ("template_signers", "facsimile_scale", "REAL DEFAULT 1"),
    ]

    if dialect == "postgresql":
        with engine.begin() as conn:
            for table, col, coltype in alters_pg:
                if not _pg_column_exists(conn, table, col):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
    elif dialect == "sqlite":
        with engine.begin() as conn:
            for table, col, coltype in alters_sqlite:
                if col not in _sqlite_columns(conn, table):
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))


def ensure_tpmpk_bot_question_columns(engine: Engine) -> None:
    dialect = engine.dialect.name

    if dialect == "postgresql":
        with engine.begin() as conn:
            if not _pg_column_exists(conn, "tpmpk_appointment", "child_registered_irkutsk"):
                conn.execute(text("ALTER TABLE tpmpk_appointment ADD COLUMN child_registered_irkutsk BOOLEAN"))
                conn.execute(text("UPDATE tpmpk_appointment SET child_registered_irkutsk = TRUE WHERE child_registered_irkutsk IS NULL"))
                conn.execute(text("ALTER TABLE tpmpk_appointment ALTER COLUMN child_registered_irkutsk SET NOT NULL"))
            if not _pg_column_exists(conn, "tpmpk_appointment", "document_readiness"):
                conn.execute(text("ALTER TABLE tpmpk_appointment ADD COLUMN document_readiness VARCHAR(40)"))
                conn.execute(text("UPDATE tpmpk_appointment SET document_readiness = 'full' WHERE document_readiness IS NULL"))
                conn.execute(text("ALTER TABLE tpmpk_appointment ALTER COLUMN document_readiness SET NOT NULL"))
    elif dialect == "sqlite":
        with engine.begin() as conn:
            columns = _sqlite_columns(conn, "tpmpk_appointment")
            if "child_registered_irkutsk" not in columns:
                conn.execute(text("ALTER TABLE tpmpk_appointment ADD COLUMN child_registered_irkutsk BOOLEAN NOT NULL DEFAULT 1"))
            if "document_readiness" not in columns:
                conn.execute(text("ALTER TABLE tpmpk_appointment ADD COLUMN document_readiness TEXT NOT NULL DEFAULT 'full'"))


def ensure_article_editor_columns(engine: Engine) -> None:
    dialect = engine.dialect.name

    alters_pg: list[tuple[str, str]] = [
        ("status", "VARCHAR(20) DEFAULT 'draft' NOT NULL"),
        ("excerpt", "VARCHAR(800)"),
        ("image", "VARCHAR(500)"),
        ("lead", "VARCHAR(800)"),
        ("body", "TEXT DEFAULT '' NOT NULL"),
        ("cover_image_url", "VARCHAR(500)"),
        ("is_pinned", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("duplicate_to_main", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("duplicate_to_events", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("blocks", "JSONB DEFAULT '[]'::jsonb NOT NULL"),
        ("attachments", "JSONB DEFAULT '[]'::jsonb NOT NULL"),
        ("categories", "JSONB DEFAULT '[]'::jsonb NOT NULL"),
        ("tags", "JSONB DEFAULT '[]'::jsonb NOT NULL"),
        ("publishing_scope", "VARCHAR(20) DEFAULT 'both' NOT NULL"),
        ("methodika_subject", "VARCHAR(120)"),
        ("dom_uchitelya_section", "VARCHAR(120)"),
        ("noko_section", "VARCHAR(120)"),
        ("hub_kind", "VARCHAR(64)"),
        ("hub_path", "VARCHAR(160)"),
        ("published_at", "TIMESTAMP WITH TIME ZONE"),
    ]
    alters_sqlite: list[tuple[str, str]] = [
        ("status", "TEXT DEFAULT 'draft' NOT NULL"),
        ("excerpt", "TEXT"),
        ("image", "TEXT"),
        ("lead", "TEXT"),
        ("body", "TEXT DEFAULT '' NOT NULL"),
        ("cover_image_url", "TEXT"),
        ("is_pinned", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("duplicate_to_main", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("duplicate_to_events", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("blocks", "TEXT DEFAULT '[]' NOT NULL"),
        ("attachments", "TEXT DEFAULT '[]' NOT NULL"),
        ("categories", "TEXT DEFAULT '[]' NOT NULL"),
        ("tags", "TEXT DEFAULT '[]' NOT NULL"),
        ("publishing_scope", "TEXT DEFAULT 'both' NOT NULL"),
        ("methodika_subject", "TEXT"),
        ("dom_uchitelya_section", "TEXT"),
        ("noko_section", "TEXT"),
        ("hub_kind", "TEXT"),
        ("hub_path", "TEXT"),
        ("published_at", "TIMESTAMP"),
    ]

    if dialect == "postgresql":
        with engine.begin() as conn:
            table_exists = conn.execute(text("SELECT to_regclass('public.article')")).scalar()
            if not table_exists:
                return
            for col, coltype in alters_pg:
                if not _pg_column_exists(conn, "article", col):
                    conn.execute(text(f"ALTER TABLE article ADD COLUMN {col} {coltype}"))
    elif dialect == "sqlite":
        with engine.begin() as conn:
            tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='article'")).fetchall()
            if not tables:
                return
            columns = _sqlite_columns(conn, "article")
            for col, coltype in alters_sqlite:
                if col not in columns:
                    conn.execute(text(f"ALTER TABLE article ADD COLUMN {col} {coltype}"))


def ensure_tpmpk_slot_minutes_range(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    constraints = [
        ("tpmpk_schedule_template", "tpmpk_schedule_template_slot_minutes_chk"),
        ("tpmpk_working_day", "tpmpk_working_day_slot_minutes_chk"),
    ]
    with engine.begin() as conn:
        for table, constraint in constraints:
            conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"))
            conn.execute(
                text(
                    f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                    "CHECK (slot_minutes BETWEEN 10 AND 240 AND slot_minutes % 5 = 0)"
                )
            )
