"""Add validation_issues; make the vendor invoice-number index apply to approved invoices only.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_issues",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "invoice_id",
            sa.Integer,
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field", sa.String(100), nullable=False),
        sa.Column("rule_code", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("resolved", sa.Boolean, server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "severity IN ('error', 'warning')", name="ck_validation_issues_severity"
        ),
    )
    op.create_index("ix_validation_issues_invoice_id", "validation_issues", ["invoice_id"])

    # Duplicates may now be saved for review; only the approved ledger must be unique.
    op.drop_index("uq_invoices_user_vendor_number", table_name="invoices")
    op.create_index(
        "uq_invoices_user_vendor_number",
        "invoices",
        ["user_id", "vendor_gstin", "invoice_number"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )


def downgrade() -> None:
    op.drop_index("uq_invoices_user_vendor_number", table_name="invoices")
    op.create_index(
        "uq_invoices_user_vendor_number",
        "invoices",
        ["user_id", "vendor_gstin", "invoice_number"],
        unique=True,
    )
    op.drop_table("validation_issues")
