"""render main compatibility head

Revision ID: e0f1a2b3c4d5
Revises: 19f2a4c6d8e1, 3f2a8c9d1e6b, 7a9c2e4f6b8d
Create Date: 2026-06-30
"""

from typing import Sequence, Union


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = (
    "19f2a4c6d8e1",
    "3f2a8c9d1e6b",
    "7a9c2e4f6b8d",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
