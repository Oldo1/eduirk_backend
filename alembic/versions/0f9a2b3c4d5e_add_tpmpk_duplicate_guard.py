"""add tpmpk duplicate guard

Revision ID: 0f9a2b3c4d5e
Revises: b4f1c2d3e4a5
Create Date: 2026-05-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0f9a2b3c4d5e"
down_revision = "b4f1c2d3e4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tpmpk_appointment", sa.Column("duplicate_key", sa.String(length=64), nullable=True))
    op.create_index(
        "tpmpk_appointment_duplicate_active_uniq",
        "tpmpk_appointment",
        ["duplicate_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelled' AND duplicate_key IS NOT NULL"),
        sqlite_where=sa.text("status <> 'cancelled' AND duplicate_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("tpmpk_appointment_duplicate_active_uniq", table_name="tpmpk_appointment")
    op.drop_column("tpmpk_appointment", "duplicate_key")
