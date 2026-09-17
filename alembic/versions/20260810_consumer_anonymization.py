"""Add consumer anonymization timestamp.

Revision ID: 20260810_consumer_anonymization
Revises: 20260810_lottery_record_client_context
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_consumer_anonymization"
down_revision = "20260810_lottery_record_client_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consumer", sa.Column("anonymized_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("consumer", "anonymized_at")
