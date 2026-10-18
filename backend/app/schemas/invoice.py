from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class InvoiceFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    mime_type: str
    page_count: int
    sha256: str


class InvoiceUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    file: InvoiceFileResponse


class LineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    description: str | None
    hsn_sac_code: str | None
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    discount: Decimal | None
    taxable_value: Decimal | None
    gst_rate: Decimal | None
    cgst_amount: Decimal | None
    sgst_amount: Decimal | None
    igst_amount: Decimal | None
    line_total: Decimal | None
    confidence: float | None


class ValidationIssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_code: str
    field: str
    severity: str
    message: str
    resolved: bool


class ExtractionSummary(BaseModel):
    """How extraction went, without the raw model output."""

    attempts: int
    total_cost_usd: Decimal | None
    total_latency_ms: int
    issues: list[dict] | None  # from the last attempt, if it was rejected


class InvoiceDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    file: InvoiceFileResponse
    vendor_name: str | None
    vendor_gstin: str | None
    buyer_name: str | None
    buyer_gstin: str | None
    invoice_number: str | None
    invoice_date: date | None
    place_of_supply: str | None
    subtotal: Decimal | None
    cgst: Decimal | None
    sgst: Decimal | None
    igst: Decimal | None
    total: Decimal | None
    currency: str
    overall_confidence: float | None
    line_items: list[LineItemResponse]
    validation_issues: list[ValidationIssueResponse]
    extraction: ExtractionSummary | None = None
