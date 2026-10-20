from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class MatchStatus:
    MATCHED = "matched"  # same supplier and number; date and amounts agree
    MISMATCHED = "mismatched"  # same supplier and number; something differs
    POSSIBLE = "possible"  # same supplier and amounts, number written differently
    ONLY_2B = "only_2b"  # reported by the supplier, not in the approved books


class Gstr2bStatement(Base):
    """One month's GSTR-2B for one user (re-uploading the month replaces it)."""

    __tablename__ = "gstr2b_statements"
    __table_args__ = (
        UniqueConstraint("user_id", "period", name="uq_gstr2b_statements_user_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period: Mapped[date] = mapped_column(Date)  # first day of the return month
    recipient_gstin: Mapped[str | None] = mapped_column(String(15))
    source_format: Mapped[str] = mapped_column(String(40))
    file_name: Mapped[str] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    invoices: Mapped[list["Gstr2bInvoice"]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", order_by="Gstr2bInvoice.id"
    )


class Gstr2bInvoice(Base):
    """A supplier invoice as reported in GSTR-2B, with its matching result."""

    __tablename__ = "gstr2b_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("gstr2b_statements.id", ondelete="CASCADE"), index=True
    )
    supplier_gstin: Mapped[str] = mapped_column(String(15))
    supplier_name: Mapped[str | None] = mapped_column(String(255))
    invoice_number: Mapped[str] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    invoice_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)
    itc_available: Mapped[bool] = mapped_column(Boolean, default=True)
    taxable_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    igst: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cgst: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    sgst: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cess: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    match_status: Mapped[str] = mapped_column(String(20), default=MatchStatus.ONLY_2B)
    matched_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), index=True
    )
    differences: Mapped[list | None] = mapped_column(JSON)
    statement: Mapped[Gstr2bStatement] = relationship(back_populates="invoices")
