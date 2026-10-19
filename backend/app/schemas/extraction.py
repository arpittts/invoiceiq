"""Invoice extraction schema (blueprint section 8).

Two layers:

1. The *model output* (`ExtractionOutput`): what Claude fills in. Every value is the
   text exactly as printed (or null), plus a confidence. Plain strings keep the JSON
   schema simple and stop the model from "helpfully" rounding or reformatting numbers.

2. The *typed result* (`InvoiceExtraction`): what the rest of the app uses. Our own
   code converts the printed text into Decimal amounts, dates and rates. Bad values
   raise ValidationError, which the extraction service turns into a retry.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field, ValidationError, field_validator

__all__ = [
    "ExtractionOutput",
    "InvoiceExtraction",
    "ValidationError",
    "parse_amount",
    "parse_date",
    "parse_rate",
]


# ---------------------------------------------------------------------------
# 1. What the model returns
# ---------------------------------------------------------------------------


class PrintedValue(BaseModel):
    """One field as printed on the invoice."""

    value: str | None = Field(
        description="The text exactly as printed, or null if the field is not on the invoice."
    )
    confidence: float = Field(
        description="How sure you are that `value` is correct, from 0.0 to 1.0."
    )


class LineItemOutput(BaseModel):
    description: PrintedValue
    hsn_sac_code: PrintedValue
    quantity: PrintedValue
    unit: PrintedValue
    unit_price: PrintedValue
    discount: PrintedValue
    taxable_value: PrintedValue
    gst_rate: PrintedValue
    cgst_amount: PrintedValue
    sgst_amount: PrintedValue
    igst_amount: PrintedValue
    line_total: PrintedValue


class ExtractionOutput(BaseModel):
    vendor_name: PrintedValue
    vendor_gstin: PrintedValue
    buyer_name: PrintedValue
    buyer_gstin: PrintedValue
    invoice_number: PrintedValue
    invoice_date: PrintedValue
    place_of_supply: PrintedValue
    line_items: list[LineItemOutput]
    subtotal: PrintedValue
    cgst: PrintedValue
    sgst: PrintedValue
    igst: PrintedValue
    total: PrintedValue


# ---------------------------------------------------------------------------
# Parsing printed text into typed values
# ---------------------------------------------------------------------------

# Money columns are Numeric(14, 2): anything this big can't be stored.
MAX_AMOUNT = Decimal("1e12")

# Currency markers that appear on Indian invoices.
_CURRENCY = re.compile(r"(₹|rs\.?|inr)", re.IGNORECASE)


def parse_amount(text: str | None) -> Decimal | None:
    """'₹1,23,456.50' -> Decimal('123456.50'). Handles Indian digit grouping."""
    if text is None or not text.strip():
        return None
    cleaned = _CURRENCY.sub("", text).replace(",", "").replace(" ", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")  # accounting style (500)
    cleaned = cleaned.strip("()")
    if cleaned.endswith("/-"):  # "1,200/-" is a common Indian way to write whole rupees
        cleaned = cleaned[:-2]
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not an amount: {text!r}") from None
    if not amount.is_finite():
        raise ValueError(f"not an amount: {text!r}")
    if abs(amount) >= MAX_AMOUNT:  # larger than the database column can hold: a misread
        raise ValueError(f"amount too large: {text!r}")
    return -amount if negative else amount


def parse_rate(text: str | None) -> Decimal | None:
    """'18%' or '18.00 %' -> Decimal('18')."""
    if text is None or not text.strip():
        return None
    rate = parse_amount(text.replace("%", ""))
    if rate is not None and not (0 <= rate <= 100):
        raise ValueError(f"GST rate out of range: {text!r}")
    return rate.normalize() if rate is not None else None


# Indian invoices write dates day-first, so 03/04/2026 is 3 April, never 4 March.
_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    "%Y-%m-%d",
    "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d-%B-%Y", "%d %b, %Y", "%d %B, %Y",
    "%d %b %y", "%d-%b-%y", "%d/%b/%y",
    "%b %d, %Y", "%B %d, %Y",
]  # fmt: skip


def parse_date(text: str | None) -> date | None:
    if text is None or not text.strip():
        return None
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.strip())  # "3rd Oct" -> "3 Oct"
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {text!r}")


def _clean_text(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text or None


def _clean_code(text: str | None) -> str | None:
    """GSTINs and HSN codes: drop spaces, uppercase."""
    if text is None:
        return None
    return text.replace(" ", "").upper() or None


# ---------------------------------------------------------------------------
# 2. Typed result used by the rest of the app
# ---------------------------------------------------------------------------


class Confident(BaseModel):
    confidence: float

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value


class TextField(Confident):
    value: str | None


class AmountField(Confident):
    value: Decimal | None


class DateField(Confident):
    value: date | None


class LineItem(BaseModel):
    description: TextField
    hsn_sac_code: TextField
    quantity: AmountField
    unit: TextField
    unit_price: AmountField
    discount: AmountField
    taxable_value: AmountField
    gst_rate: AmountField
    cgst_amount: AmountField
    sgst_amount: AmountField
    igst_amount: AmountField
    line_total: AmountField


class InvoiceExtraction(BaseModel):
    vendor_name: TextField
    vendor_gstin: TextField
    buyer_name: TextField
    buyer_gstin: TextField
    invoice_number: TextField
    invoice_date: DateField
    place_of_supply: TextField
    line_items: list[LineItem]
    subtotal: AmountField
    cgst: AmountField
    sgst: AmountField
    igst: AmountField
    total: AmountField

    def confidences(self) -> list[float]:
        """Every field's confidence, including line items."""
        scores = []
        for name in type(self).model_fields:
            field = getattr(self, name)
            if isinstance(field, Confident):
                scores.append(field.confidence)
        for item in self.line_items:
            scores.extend(getattr(item, name).confidence for name in LineItem.model_fields)
        return scores

    @property
    def overall_confidence(self) -> float:
        """The weakest field decides: one shaky digit makes the whole invoice shaky."""
        scores = self.confidences()
        return min(scores) if scores else 0.0


# Which converter to use for each field.
_AMOUNT_FIELDS = {
    "quantity", "unit_price", "discount", "taxable_value", "cgst_amount", "sgst_amount",
    "igst_amount", "line_total", "subtotal", "cgst", "sgst", "igst", "total",
}  # fmt: skip
_CODE_FIELDS = {"vendor_gstin", "buyer_gstin", "hsn_sac_code"}


def _convert_field(name: str, printed: PrintedValue) -> dict:
    """Turn one PrintedValue into the dict for its typed field. Errors name the field."""
    try:
        if name in _AMOUNT_FIELDS:
            value = parse_amount(printed.value)
        elif name == "gst_rate":
            value = parse_rate(printed.value)
        elif name == "invoice_date":
            value = parse_date(printed.value)
        elif name in _CODE_FIELDS:
            value = _clean_code(printed.value)
        else:
            value = _clean_text(printed.value)
    except ValueError as error:
        raise ValueError(f"{name}: {error}") from None
    return {"value": value, "confidence": printed.confidence}


def to_invoice_extraction(output: ExtractionOutput) -> InvoiceExtraction:
    """Convert model output into typed values.

    Raises ValueError (bad printed value) or ValidationError (e.g. confidence out of
    range). Both mean "ask the model again"; the message says which field was wrong.
    """
    data: dict = {}
    for name in ExtractionOutput.model_fields:
        if name == "line_items":
            continue
        data[name] = _convert_field(name, getattr(output, name))

    data["line_items"] = []
    for index, item in enumerate(output.line_items, start=1):
        try:
            data["line_items"].append(
                {
                    name: _convert_field(name, getattr(item, name))
                    for name in LineItemOutput.model_fields
                }
            )
        except ValueError as error:
            raise ValueError(f"line item {index}: {error}") from None

    return InvoiceExtraction.model_validate(data)
