"""Add client context to lottery records.

Revision ID: 20260810_lottery_record_client_context
Revises: 20260809_dealer_direct_replenishment
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260810_lottery_record_client_context"
down_revision = "20260809_dealer_replenish"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("lottery_record")}
    if "client_ip" not in columns:
        op.add_column("lottery_record", sa.Column("client_ip", sa.String(length=64), nullable=True))
    if "client_location" not in columns:
        op.add_column("lottery_record", sa.Column("client_location", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("lottery_record", "client_location")
    op.drop_column("lottery_record", "client_ip")
