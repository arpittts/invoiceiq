from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExtractionRun(Base):
    """One LLM extraction attempt for an invoice (blueprint section 7).

    Every attempt is kept, including failed ones, so cost and failure rates are honest.
    """

    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(100))
    # The model's JSON exactly as returned (None if it returned nothing usable).
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    # Why this attempt was rejected, e.g. [{"code": "SCHEMA", "message": "..."}].
    issues_json: Mapped[list | None] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
