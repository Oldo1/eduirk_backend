"""add assistant runtime settings

Revision ID: 19f2a4c6d8e1
Revises: b4f1c2d3e4a5
Create Date: 2026-06-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "19f2a4c6d8e1"
down_revision: Union[str, None] = "b4f1c2d3e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistant_runtime_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("update_interval_hours", sa.Float(), nullable=False),
        sa.Column("gigachat_model", sa.String(length=64), nullable=False),
        sa.Column("question_max_length", sa.Integer(), nullable=False),
        sa.Column("session_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("history_max_messages", sa.Integer(), nullable=False),
        sa.Column("rate_limit_window_seconds", sa.Integer(), nullable=False),
        sa.Column("rate_limit_max_requests", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("assistant_runtime_settings")
