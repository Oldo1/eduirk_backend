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


def ensure_certificate_layout_columns(engine: Engine) -> None:
    dialect = engine.dialect.name

    alters_pg: list[tuple[str, str, str]] = [
        ("certificate_templates", "signers_block_x_mm", "DOUBLE PRECISION DEFAULT 105"),
        ("certificate_templates", "signers_row_height_mm", "DOUBLE PRECISION DEFAULT 32"),
        ("certificate_templates", "signers_band_width_mm", "DOUBLE PRECISION DEFAULT 168"),
        ("certificate_templates", "signers_font_size", "DOUBLE PRECISION DEFAULT 10"),
        ("certificate_templates", "signers_text_color", "VARCHAR(16) DEFAULT '#1e293b'"),
        ("certificate_templates", "signers_font_weight", "VARCHAR(8) DEFAULT '400'"),
        ("certificate_templates", "margin_left_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("certificate_templates", "margin_right_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("certificate_templates", "margin_top_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("certificate_templates", "margin_bottom_mm", "DOUBLE PRECISION DEFAULT 12"),
        ("template_text_elements", "max_width_mm", "DOUBLE PRECISION"),
        ("template_text_elements", "max_height_mm", "DOUBLE PRECISION"),
        ("template_signers", "offset_y_mm", "DOUBLE PRECISION DEFAULT 0"),
        ("template_signers", "facsimile_offset_x_mm", "DOUBLE PRECISION DEFAULT 0"),
        ("template_signers", "facsimile_offset_y_mm", "DOUBLE PRECISION DEFAULT 0"),
        ("template_signers", "facsimile_scale", "DOUBLE PRECISION DEFAULT 1"),
    ]

    alters_sqlite: list[tuple[str, str, str]] = [
        ("certificate_templates", "signers_block_x_mm", "REAL DEFAULT 105"),
        ("certificate_templates", "signers_row_height_mm", "REAL DEFAULT 32"),
        ("certificate_templates", "signers_band_width_mm", "REAL DEFAULT 168"),
        ("certificate_templates", "signers_font_size", "REAL DEFAULT 10"),
        ("certificate_templates", "signers_text_color", "TEXT DEFAULT '#1e293b'"),
        ("certificate_templates", "signers_font_weight", "TEXT DEFAULT '400'"),
        ("certificate_templates", "margin_left_mm", "REAL DEFAULT 12"),
        ("certificate_templates", "margin_right_mm", "REAL DEFAULT 12"),
        ("certificate_templates", "margin_top_mm", "REAL DEFAULT 12"),
        ("certificate_templates", "margin_bottom_mm", "REAL DEFAULT 12"),
        ("template_text_elements", "max_width_mm", "REAL"),
        ("template_text_elements", "max_height_mm", "REAL"),
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
