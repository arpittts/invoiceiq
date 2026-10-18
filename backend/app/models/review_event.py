from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReviewEvent(Base):
    """One human action on an invoice (blueprint section 7): an audit trail and,
    later, evaluation data showing where the extractor was corrected.

    Examples of `field`: "total", "line_items[2].gst_rate", "status",
    "issue:TAX_MATH:line_items[1].gst_rate" (an issue dismissed by the reviewer).
    """

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(150))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
