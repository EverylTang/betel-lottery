"""Add per-consumer winning probability multiplier.

Revision ID: 20260811_consumer_win_probability
Revises: 20260811_remove_activity_draw_limits
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_consumer_win_probability"
down_revision = "20260811_remove_activity_draw_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consumer", sa.Column("win_probability_multiplier", sa.Numeric(5, 4), nullable=False, server_default="1.0000"))


def downgrade() -> None:
    op.drop_column("consumer", "win_probability_multiplier")
