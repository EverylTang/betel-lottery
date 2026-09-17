"""add payment method to upgrade orders

Revision ID: 20260908_upgrade_redemption_closure
Revises: 20260812_dealer_redemptions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260908_upgrade_redemption_closure"
down_revision = "20260812_dealer_redemptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    if not inspector.has_table("upgrade_order"):
        return
    if "payment_method" not in {column["name"] for column in inspector.get_columns("upgrade_order")}:
        op.add_column("upgrade_order", sa.Column("payment_method", sa.String(length=32), nullable=False, server_default="manual"))


def downgrade() -> None:
    op.drop_column("upgrade_order", "payment_method")
