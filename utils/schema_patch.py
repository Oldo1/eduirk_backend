"""
Добавление колонок для расширенной раскладки грамот (существующие БД без Alembic).
Поддерживается PostgreSQL; для SQLite можно расширить при необходимости.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


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
