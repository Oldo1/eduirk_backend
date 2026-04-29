"""add_tpmpk_bot_questions

Revision ID: b73f2d91c4d8
Revises: 68014c9be4a5
Create Date: 2026-04-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b73f2d91c4d8"
down_revision: Union[str, Sequence[str], None] = "68014c9be4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tpmpk_appointment",
        sa.Column("child_registered_irkutsk", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "tpmpk_appointment",
        sa.Column("document_readiness", sa.String(length=40), nullable=True),
    )
    op.execute("UPDATE tpmpk_appointment SET child_registered_irkutsk = TRUE WHERE child_registered_irkutsk IS NULL")
    op.execute("UPDATE tpmpk_appointment SET document_readiness = 'full' WHERE document_readiness IS NULL")
    op.alter_column("tpmpk_appointment", "child_registered_irkutsk", nullable=False)
    op.alter_column("tpmpk_appointment", "document_readiness", nullable=False)
    op.create_check_constraint(
        "tpmpk_appointment_document_readiness_chk",
        "tpmpk_appointment",
        "document_readiness IN ('full', 'not_ready', 'psychiatrist_consultation')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "tpmpk_appointment_document_readiness_chk",
        "tpmpk_appointment",
        type_="check",
    )
    op.drop_column("tpmpk_appointment", "document_readiness")
    op.drop_column("tpmpk_appointment", "child_registered_irkutsk")
