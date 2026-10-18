from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    id: int
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
    approved_at: datetime | None = None
    extraction: ExtractionSummary | None = None
    # How sure the model was about each field, e.g. {"total": 0.95,
    # "line_items[1].quantity": 0.8}. Taken from the last valid extraction.
    field_confidence: dict[str, float] = {}


class InvoiceSummary(BaseModel):
    """One row of the invoice list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    original_name: str
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total: Decimal | None
    overall_confidence: float | None
    open_errors: int
    open_warnings: int


class InvoiceListResponse(BaseModel):
    items: list[InvoiceSummary]
    total: int


class LineItemUpdate(BaseModel):
    """A line item as the reviewer wants it saved (the whole list is sent)."""

    description: str | None = Field(default=None, max_length=2000)
    hsn_sac_code: str | None = Field(default=None, max_length=10)
    quantity: Decimal | None = None
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Decimal | None = None
    discount: Decimal | None = None
    taxable_value: Decimal | None = None
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100)
    cgst_amount: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_amount: Decimal | None = None
    line_total: Decimal | None = None


class InvoiceUpdate(BaseModel):
    """Reviewer corrections. Only the fields that are sent are changed."""

    vendor_name: str | None = Field(default=None, max_length=255)
    vendor_gstin: str | None = None
    buyer_name: str | None = Field(default=None, max_length=255)
    buyer_gstin: str | None = None
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    place_of_supply: str | None = Field(default=None, max_length=100)
    subtotal: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItemUpdate] | None = None

    @field_validator("vendor_gstin", "buyer_gstin")
    @classmethod
    def clean_gstin(cls, value: str | None) -> str | None:
        """Accept ' 27aapfu 0939f1zv ' as typed: drop spaces, uppercase, then check length."""
        if value is None:
            return None
        cleaned = "".join(value.split()).upper()
        if len(cleaned) > 15:
            raise ValueError("A GSTIN has 15 characters")
        return cleaned or None
