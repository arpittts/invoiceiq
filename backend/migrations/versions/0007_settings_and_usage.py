"""Per-user auto-approve settings, and a daily LLM usage counter for the cost cap.

- users: three nullable settings. NULL means "use the server default"
  (AUTO_APPROVE_ENABLED and the two thresholds), so existing users keep today's behaviour.
- llm_usage_daily: one row per day (India time) counting real model calls and
  their cost. It lives in the database, not in memory, so a restart of the free
  host can't reset the cap.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auto_approve_enabled", sa.Boolean))
    op.add_column("users", sa.Column("auto_approve_min_confidence", sa.Float))
    op.add_column("users", sa.Column("auto_approve_min_hsn_confidence", sa.Float))
    op.create_table(
        "llm_usage_daily",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("llm_usage_daily")
    op.drop_column("users", "auto_approve_min_hsn_confidence")
    op.drop_column("users", "auto_approve_min_confidence")
    op.drop_column("users", "auto_approve_enabled")
