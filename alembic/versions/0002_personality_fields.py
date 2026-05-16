"""add personality fields to users

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_personality_update", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("personality_data", postgresql.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "personality_data")
    op.drop_column("users", "last_personality_update")
