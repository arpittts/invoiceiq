"""Demo accounts: "Try the demo" creates a throwaway user, deleted after 24 hours.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_demo", sa.Boolean, nullable=False, server_default="false")
    )
    op.create_index(
        "ix_users_demo_created_at",
        "users",
        ["created_at"],
        postgresql_where=sa.text("is_demo"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_demo_created_at", table_name="users")
    op.drop_column("users", "is_demo")
