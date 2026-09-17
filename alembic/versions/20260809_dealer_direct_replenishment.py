"""Allow dealer-direct replenishment tasks without a store.

Revision ID: 20260809_dealer_replenish
Revises: 20260809_operational_rewards
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260809_dealer_replenish"
down_revision = "20260809_operational_rewards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if inspect(connection).has_table("replenishment_task"):
        op.alter_column(
            "replenishment_task",
            "store_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    op.alter_column(
        "replenishment_task",
        "store_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
