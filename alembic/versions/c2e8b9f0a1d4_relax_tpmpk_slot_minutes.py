"""Relax TPMPK slot duration constraints.

Revision ID: c2e8b9f0a1d4
Revises: b73f2d91c4d8
Create Date: 2026-04-28 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c2e8b9f0a1d4"
down_revision: Union[str, Sequence[str], None] = "b73f2d91c4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("tpmpk_schedule_template_slot_minutes_chk", "tpmpk_schedule_template", type_="check")
    op.create_check_constraint(
        "tpmpk_schedule_template_slot_minutes_chk",
        "tpmpk_schedule_template",
        "slot_minutes BETWEEN 10 AND 240 AND slot_minutes % 5 = 0",
    )
    op.drop_constraint("tpmpk_working_day_slot_minutes_chk", "tpmpk_working_day", type_="check")
    op.create_check_constraint(
        "tpmpk_working_day_slot_minutes_chk",
        "tpmpk_working_day",
        "slot_minutes BETWEEN 10 AND 240 AND slot_minutes % 5 = 0",
    )


def downgrade() -> None:
    op.drop_constraint("tpmpk_schedule_template_slot_minutes_chk", "tpmpk_schedule_template", type_="check")
    op.create_check_constraint(
        "tpmpk_schedule_template_slot_minutes_chk",
        "tpmpk_schedule_template",
        "slot_minutes IN (30, 60)",
    )
    op.drop_constraint("tpmpk_working_day_slot_minutes_chk", "tpmpk_working_day", type_="check")
    op.create_check_constraint(
        "tpmpk_working_day_slot_minutes_chk",
        "tpmpk_working_day",
        "slot_minutes IN (30, 60)",
    )
