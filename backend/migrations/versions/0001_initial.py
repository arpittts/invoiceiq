"""Initial schema: users, invoice_files, file_blobs, invoices, line_items.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def money(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(14, 2), nullable=True)


def upgrade() -> None:
    # pgvector is needed from Phase 5 (hsn_codes.embedding); enabling it now
    # proves early that the production database supports it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "invoice_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("page_count", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
    )
    op.create_index("ix_invoice_files_user_id", "invoice_files", ["user_id"])
    op.create_index("ix_invoice_files_sha256", "invoice_files", ["sha256"])

    op.create_table(
        "file_blobs",
        sa.Column(
            "file_id",
            sa.Integer,
            sa.ForeignKey("invoice_files.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("data", sa.LargeBinary, nullable=False),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("invoice_files.id"), nullable=False),
        sa.Column("vendor_name", sa.String(255)),
        sa.Column("vendor_gstin", sa.String(15)),
        sa.Column("buyer_name", sa.String(255)),
        sa.Column("buyer_gstin", sa.String(15)),
        sa.Column("invoice_number", sa.String(100)),
        sa.Column("invoice_date", sa.Date),
        sa.Column("place_of_supply", sa.String(100)),
        money("subtotal"),
        money("cgst"),
        money("sgst"),
        money("igst"),
        money("total"),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("overall_confidence", sa.Float),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('processing', 'needs_review', 'approved', 'rejected', 'failed')",
            name="ck_invoices_status",
        ),
    )
    op.create_index("ix_invoices_user_id", "invoices", ["user_id"])
    op.create_index(
        "uq_invoices_user_vendor_number",
        "invoices",
        ["user_id", "vendor_gstin", "invoice_number"],
        unique=True,
    )

    op.create_table(
        "line_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "invoice_id",
            sa.Integer,
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("quantity", sa.Numeric(14, 3)),
        sa.Column("unit", sa.String(20)),
        money("unit_price"),
        money("discount"),
        money("taxable_value"),
        sa.Column("gst_rate", sa.Numeric(5, 2)),
        sa.Column("hsn_sac_code", sa.String(10)),
        sa.Column("hsn_source_id", sa.Integer),
        money("cgst_amount"),
        money("sgst_amount"),
        money("igst_amount"),
        money("line_total"),
        sa.Column("confidence", sa.Float),
    )
    op.create_index("ix_line_items_invoice_id", "line_items", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("line_items")
    op.drop_table("invoices")
    op.drop_table("file_blobs")
    op.drop_table("invoice_files")
    op.drop_table("users")
    # The vector extension is left in place; other database objects may use it later.
