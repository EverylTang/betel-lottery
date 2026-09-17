"""Add non-physical reward fulfillment and consumer risk fields.

Revision ID: 20260809_operational_rewards
Revises:
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260809_operational_rewards"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prize", sa.Column("points_amount", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("consumer", sa.Column("risk_status", sa.String(length=16), nullable=False, server_default="normal"))
    op.add_column("consumer", sa.Column("risk_note", sa.String(length=512), nullable=False, server_default=""))
    op.create_index("ix_consumer_risk_status", "consumer", ["risk_status"])
    op.add_column("cash_payment", sa.Column("processed_at", sa.DateTime(), nullable=True))
    op.add_column("cash_payment", sa.Column("operator_note", sa.String(length=512), nullable=False, server_default=""))
    op.create_table(
        "coupon_code",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prize_id", sa.Integer(), sa.ForeignKey("prize.id"), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="available"),
        sa.Column("lottery_record_id", sa.Integer(), sa.ForeignKey("lottery_record.id"), unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_coupon_code_prize_id", "coupon_code", ["prize_id"])
    op.create_index("ix_coupon_code_status", "coupon_code", ["status"])
    op.create_table(
        "point_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consumer_id", sa.Integer(), sa.ForeignKey("consumer.id"), nullable=False),
        sa.Column("lottery_record_id", sa.Integer(), sa.ForeignKey("lottery_record.id"), nullable=False, unique=True),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=False, server_default="抽奖积分奖励"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_point_ledger_consumer_id", "point_ledger", ["consumer_id"])
    op.add_column("lottery_record", sa.Column("is_retry", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint("uq_lottery_qrcode", "lottery_record", type_="unique")
    op.create_table(
        "scan_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consumer_id", sa.Integer(), sa.ForeignKey("consumer.id"), nullable=True),
        sa.Column("qrcode_id", sa.Integer(), sa.ForeignKey("qrcode.id"), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False, server_default="accepted"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scan_event_consumer_id", "scan_event", ["consumer_id"])
    op.create_index("ix_scan_event_qrcode_id", "scan_event", ["qrcode_id"])
    op.create_index("ix_scan_event_result", "scan_event", ["result"])


def downgrade() -> None:
    op.drop_table("scan_event")
    op.create_unique_constraint("uq_lottery_qrcode", "lottery_record", ["qrcode_id"])
    op.drop_column("lottery_record", "is_retry")
    op.drop_table("point_ledger")
    op.drop_table("coupon_code")
    op.drop_column("cash_payment", "operator_note")
    op.drop_column("cash_payment", "processed_at")
    op.drop_index("ix_consumer_risk_status", table_name="consumer")
    op.drop_column("consumer", "risk_note")
    op.drop_column("consumer", "risk_status")
    op.drop_column("prize", "points_amount")
