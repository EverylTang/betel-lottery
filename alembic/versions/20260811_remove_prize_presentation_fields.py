"""Remove deprecated prize presentation fields.

Revision ID: 20260811_remove_prize_presentation_fields
Revises: 20260811_expand_prize_types
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op


revision = "20260811_remove_prize_presentation_fields"
down_revision = "20260811_expand_prize_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("prize")}
    for column in ("description", "image_url", "result_image_url", "redemption_instruction", "value_text"):
        if column in columns:
            op.drop_column("prize", column)


def downgrade() -> None:
    op.add_column("prize", sa.Column("description", sa.String(length=512), nullable=False, server_default=""))
    op.add_column("prize", sa.Column("image_url", sa.String(length=512), nullable=False, server_default=""))
    op.add_column("prize", sa.Column("result_image_url", sa.String(length=512), nullable=False, server_default=""))
    op.add_column("prize", sa.Column("redemption_instruction", sa.String(length=512), nullable=False, server_default=""))
    op.add_column("prize", sa.Column("value_text", sa.String(length=64), nullable=False, server_default=""))
