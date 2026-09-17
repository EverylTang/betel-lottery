"""add dealer redemption records

Revision ID: 20260812_dealer_redemptions
Revises: 20260812_lottery_record_resolved_location
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260812_dealer_redemptions"
down_revision = "20260812_lottery_record_resolved_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    if not inspector.has_table("dealer_account"):
        return
    if inspector.has_table("dealer_redemption"):
        return
    op.create_table(
        "dealer_redemption",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lottery_record_id", sa.Integer(), nullable=False),
        sa.Column("dealer_id", sa.Integer(), nullable=False),
        sa.Column("dealer_account_id", sa.Integer(), nullable=False),
        sa.Column("redemption_note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("redeemed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lottery_record_id"], ["lottery_record.id"]),
        sa.ForeignKeyConstraint(["dealer_id"], ["dealer.id"]),
        sa.ForeignKeyConstraint(["dealer_account_id"], ["dealer_account.id"]),
        sa.UniqueConstraint("lottery_record_id", name="uq_dealer_redemption_record"),
    )
    op.create_index("ix_dealer_redemption_lottery_record_id", "dealer_redemption", ["lottery_record_id"])
    op.create_index("ix_dealer_redemption_dealer_id", "dealer_redemption", ["dealer_id"])


def downgrade() -> None:
    op.drop_index("ix_dealer_redemption_dealer_id", table_name="dealer_redemption")
    op.drop_index("ix_dealer_redemption_lottery_record_id", table_name="dealer_redemption")
    op.drop_table("dealer_redemption")
