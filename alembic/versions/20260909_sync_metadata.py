"""sync SQLModel metadata with existing database

Revision ID: 20260909_sync_metadata
Revises: 20260908_upgrade_redemption_closure, 20260908_mvp_rewards_only
"""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from alembic import op
from sqlalchemy import inspect
from sqlmodel import SQLModel

from app import models


revision = "20260909_sync_metadata"
down_revision = ("20260908_upgrade_redemption_closure", "20260908_mvp_rewards_only")
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = inspect(connection)
    SQLModel.metadata.create_all(connection, checkfirst=True)
    connection.execute(__import__("sqlalchemy").text("UPDATE activity SET rules = '' WHERE rules IS NULL"))
    op.alter_column("activity", "rules", existing_type=__import__("sqlalchemy").Text(), type_=__import__("sqlalchemy").String(length=2000), existing_nullable=True, nullable=False)
    inspector = inspect(connection)
    for table in SQLModel.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if column.nullable:
                op.add_column(table.name, column.copy())
                continue
            column_copy = column.copy()
            if column_copy.server_default is None:
                type_name = column.type.__class__.__name__
                if type_name == "Boolean":
                    column_copy.server_default = "1"
                elif type_name in ("Integer", "Float", "Numeric"):
                    column_copy.server_default = "0"
                else:
                    column_copy.server_default = ""
            op.add_column(table.name, column_copy)
            op.alter_column(table.name, column.name, nullable=False, existing_type=column.type, server_default=None)


def downgrade() -> None:
    pass
