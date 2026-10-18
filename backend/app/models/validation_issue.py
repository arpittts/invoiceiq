from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ValidationIssue(Base):
    """One problem a validation rule found on an invoice (blueprint section 7)."""

    __tablename__ = "validation_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('error', 'warning')", name="ck_validation_issues_severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(100))  # e.g. "total" or "line_items[2].gst_rate"
    rule_code: Mapped[str] = mapped_column(String(30))  # e.g. "TAX_MATH"
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
