"""retire non-MVP reward types

Revision ID: 20260908_mvp_rewards_only
Revises: 20260908_upgrade_redemption_closure
"""

from alembic import op


revision = "20260908_mvp_rewards_only"
down_revision = "20260908_upgrade_redemption_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep legacy data for audit, but prevent old reward types from being
    # selected by the MVP configuration and draw services.
    op.execute("UPDATE prize SET status = 'inactive' WHERE type IN ('COUPON', 'PHYSICAL', 'POINTS', 'RETRY')")


def downgrade() -> None:
    # Historical inactive rows must be reviewed manually before reactivation.
    pass
