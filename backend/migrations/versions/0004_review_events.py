"""Add review_events: an audit trail of human corrections and decisions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "invoice_id",
            sa.Integer,
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("field", sa.String(150), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_review_events_invoice_id", "review_events", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("review_events")
