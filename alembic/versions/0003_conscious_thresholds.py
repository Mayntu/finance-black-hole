"""add conscious_thresholds to users

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("conscious_thresholds", postgresql.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "conscious_thresholds")
