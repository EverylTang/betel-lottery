"""Store TianDiTu-resolved client address on lottery records.

Revision ID: 20260812_lottery_record_resolved_location
Revises: 20260811_remove_prize_presentation_fields
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op


revision = "20260812_lottery_record_resolved_location"
down_revision = "20260811_remove_prize_presentation_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lottery_record", sa.Column("client_province", sa.String(length=64), nullable=True))
    op.add_column("lottery_record", sa.Column("client_city", sa.String(length=64), nullable=True))
    op.add_column("lottery_record", sa.Column("client_district", sa.String(length=64), nullable=True))
    op.add_column("lottery_record", sa.Column("client_address", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("lottery_record", "client_address")
    op.drop_column("lottery_record", "client_district")
    op.drop_column("lottery_record", "client_city")
    op.drop_column("lottery_record", "client_province")
