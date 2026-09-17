"""Allow all configured prize types in MySQL.

Revision ID: 20260811_expand_prize_types
Revises: 20260811_consumer_win_probability
Create Date: 2026-08-11
"""

from alembic import op


revision = "20260811_expand_prize_types"
down_revision = "20260811_consumer_win_probability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE prize MODIFY COLUMN type ENUM('NONE','CASH','COUPON','PHYSICAL','POINTS','RETRY','UPGRADE') NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE prize MODIFY COLUMN type ENUM('NONE','CASH') NOT NULL")
