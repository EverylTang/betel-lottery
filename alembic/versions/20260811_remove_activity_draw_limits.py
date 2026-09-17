"""Remove activity participation limits.

Revision ID: 20260811_remove_activity_draw_limits
Revises: 20260811_dealer_lottery_policies
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_remove_activity_draw_limits"
down_revision = "20260811_dealer_lottery_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("activity")}
    if "total_limit_per_user" in columns:
        op.drop_column("activity", "total_limit_per_user")
    if "daily_limit_per_user" in columns:
        op.drop_column("activity", "daily_limit_per_user")


def downgrade() -> None:
    op.add_column("activity", sa.Column("daily_limit_per_user", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("activity", sa.Column("total_limit_per_user", sa.Integer(), nullable=False, server_default="20"))
