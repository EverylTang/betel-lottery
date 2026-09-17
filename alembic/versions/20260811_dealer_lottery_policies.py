"""Add versioned dealer and region lottery policies.

Revision ID: 20260811_dealer_lottery_policies
Revises: 20260810_consumer_anonymization
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_dealer_lottery_policies"
down_revision = "20260810_consumer_anonymization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lottery_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activity.id"), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("dealer_id", sa.Integer(), sa.ForeignKey("dealer.id"), nullable=True),
        sa.Column("province", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("city", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("district", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("note", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("admin_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lottery_policy_activity_id", "lottery_policy", ["activity_id"])
    op.create_index("ix_lottery_policy_dealer_id", "lottery_policy", ["dealer_id"])
    op.create_index("ix_lottery_policy_status", "lottery_policy", ["status"])
    op.create_table(
        "lottery_policy_prize",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("policy_id", sa.Integer(), sa.ForeignKey("lottery_policy.id"), nullable=False),
        sa.Column("prize_id", sa.Integer(), sa.ForeignKey("prize.id"), nullable=False),
        sa.Column("probability", sa.Numeric(7, 6), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issued_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stat_date", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("policy_id", "prize_id", name="uq_lottery_policy_prize"),
    )
    op.create_index("ix_lottery_policy_prize_policy_id", "lottery_policy_prize", ["policy_id"])
    op.create_index("ix_lottery_policy_prize_prize_id", "lottery_policy_prize", ["prize_id"])
    op.add_column("lottery_record", sa.Column("lottery_policy_id", sa.Integer(), sa.ForeignKey("lottery_policy.id"), nullable=True))
    op.add_column("lottery_record", sa.Column("lottery_policy_version", sa.Integer(), nullable=True))
    op.add_column("lottery_record", sa.Column("lottery_policy_scope", sa.String(length=16), nullable=True))
    op.create_index("ix_lottery_record_lottery_policy_id", "lottery_record", ["lottery_policy_id"])


def downgrade() -> None:
    op.drop_index("ix_lottery_record_lottery_policy_id", table_name="lottery_record")
    op.drop_column("lottery_record", "lottery_policy_scope")
    op.drop_column("lottery_record", "lottery_policy_version")
    op.drop_column("lottery_record", "lottery_policy_id")
    op.drop_table("lottery_policy_prize")
    op.drop_table("lottery_policy")
