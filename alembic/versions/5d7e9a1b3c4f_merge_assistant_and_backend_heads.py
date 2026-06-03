"""merge assistant and backend migration heads

Revision ID: 5d7e9a1b3c4f
Revises: 19f2a4c6d8e1, 3f2a8c9d1e6b
Create Date: 2026-06-02
"""

from typing import Sequence, Union


revision: str = "5d7e9a1b3c4f"
down_revision: Union[str, Sequence[str], None] = (
    "19f2a4c6d8e1",
    "3f2a8c9d1e6b",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
