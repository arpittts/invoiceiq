"""GSTR-2B reconciliation: a month's statement and its invoices, matched to the books.

- gstr2b_statements: one per user and return period (re-uploading replaces it).
- gstr2b_invoices: one row per supplier invoice in the statement, with the
  matching result: status (matched, mismatched, possible, only_2b), the approved
  invoice it matched, and which fields differ.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gstr2b_statements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period", sa.Date, nullable=False),  # first day of the return month
        sa.Column("recipient_gstin", sa.String(15)),
        sa.Column("source_format", sa.String(40), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "period", name="uq_gstr2b_statements_user_period"),
    )
    op.create_table(
        "gstr2b_invoices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "statement_id",
            sa.Integer,
            sa.ForeignKey("gstr2b_statements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("supplier_gstin", sa.String(15), nullable=False),
        sa.Column("supplier_name", sa.String(255)),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("invoice_date", sa.Date),
        sa.Column("invoice_value", sa.Numeric(14, 2)),
        sa.Column("place_of_supply", sa.String(2)),
        sa.Column("reverse_charge", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("itc_available", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("taxable_value", sa.Numeric(14, 2)),
        sa.Column("igst", sa.Numeric(14, 2)),
        sa.Column("cgst", sa.Numeric(14, 2)),
        sa.Column("sgst", sa.Numeric(14, 2)),
        sa.Column("cess", sa.Numeric(14, 2)),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="only_2b"),
        sa.Column(
            "matched_invoice_id",
            sa.Integer,
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
        ),
        sa.Column("differences", sa.JSON),
        sa.CheckConstraint(
            "match_status IN ('matched', 'mismatched', 'possible', 'only_2b')",
            name="ck_gstr2b_invoices_match_status",
        ),
    )
    op.create_index("ix_gstr2b_invoices_statement_id", "gstr2b_invoices", ["statement_id"])
    op.create_index("ix_gstr2b_invoices_matched_invoice_id", "gstr2b_invoices", ["matched_invoice_id"])


def downgrade() -> None:
    op.drop_table("gstr2b_invoices")
    op.drop_table("gstr2b_statements")
