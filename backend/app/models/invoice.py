"""Invoice, uploaded file, and line item tables (blueprint section 7)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Money is always Numeric (exact decimal), never float. 14 digits, 2 after the point.
Money = Numeric(14, 2)


class InvoiceStatus(StrEnum):
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class InvoiceFile(Base):
    __tablename__ = "invoice_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(50))
    storage_path: Mapped[str] = mapped_column(String(500))
    page_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)


class FileBlob(Base):
    """File bytes, used when STORAGE_BACKEND=db (hosts whose disks are wiped on restart)."""

    __tablename__ = "file_blobs"

    file_id: Mapped[int] = mapped_column(
        ForeignKey("invoice_files.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'needs_review', 'approved', 'rejected', 'failed')",
            name="ck_invoices_status",
        ),
        # One invoice number per vendor per user. NULLs don't clash in Postgres,
        # so invoices still being processed (no GSTIN yet) are fine.
        Index(
            "uq_invoices_user_vendor_number",
            "user_id",
            "vendor_gstin",
            "invoice_number",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.PROCESSING)
    file_id: Mapped[int] = mapped_column(ForeignKey("invoice_files.id"))

    vendor_name: Mapped[str | None] = mapped_column(String(255))
    vendor_gstin: Mapped[str | None] = mapped_column(String(15))
    buyer_name: Mapped[str | None] = mapped_column(String(255))
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    place_of_supply: Mapped[str | None] = mapped_column(String(100))

    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    cgst: Mapped[Decimal | None] = mapped_column(Money)
    sgst: Mapped[Decimal | None] = mapped_column(Money)
    igst: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    overall_confidence: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    file: Mapped[InvoiceFile] = relationship()
    line_items: Mapped[list["LineItem"]] = relationship(
        back_populates="invoice", order_by="LineItem.position", cascade="all, delete-orphan"
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    discount: Mapped[Decimal | None] = mapped_column(Money)
    taxable_value: Mapped[Decimal | None] = mapped_column(Money)
    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    hsn_sac_code: Mapped[str | None] = mapped_column(String(10))
    # Will point at hsn_codes.id once that table exists (Phase 5).
    hsn_source_id: Mapped[int | None] = mapped_column(Integer)
    cgst_amount: Mapped[Decimal | None] = mapped_column(Money)
    sgst_amount: Mapped[Decimal | None] = mapped_column(Money)
    igst_amount: Mapped[Decimal | None] = mapped_column(Money)
    line_total: Mapped[Decimal | None] = mapped_column(Money)
    confidence: Mapped[float | None] = mapped_column(Float)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
