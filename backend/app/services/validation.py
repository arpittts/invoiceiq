"""Deterministic invoice validation (blueprint section 8).

Each rule is a pure function: InvoiceData in, a list of Issues out. No AI, no
database, so every rule is easy to unit test and to explain. The LLM reads the
page; these rules check whether what it read is internally consistent.

`save_validation` at the bottom is the only part that touches the database.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceFile, InvoiceStatus, ValidationIssue
from app.services.gst import STATE_CODES, gstin_problem, state_code_of_place


def india_today() -> date:
    """Today's date in India, where invoices are dated (servers usually run on UTC)."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


# Printed amounts are rounded to paise, so allow ₹1 of rounding drift (section 8).
TOLERANCE = Decimal("1.00")
ZERO = Decimal(0)


@dataclass
class LineData:
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount: Decimal | None = None
    taxable_value: Decimal | None = None
    gst_rate: Decimal | None = None
    cgst_amount: Decimal | None = None
    sgst_amount: Decimal | None = None
    igst_amount: Decimal | None = None
    line_total: Decimal | None = None
    # Printed code and the HSN/SAC lookup's suggestion (Phase 5)
    hsn_sac_code: str | None = None
    suggested_hsn_code: str | None = None
    suggested_gst_rate: Decimal | None = None


@dataclass
class InvoiceData:
    """Just the values the rules need, independent of where they came from."""

    vendor_gstin: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    place_of_supply: str | None = None
    subtotal: Decimal | None = None
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total: Decimal | None = None
    lines: list[LineData] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "InvoiceData":
        """From plain JSON-style values (amounts as strings, dates as ISO strings),
        e.g. the ground-truth files in evals/datasets."""

        def amount(value):
            return Decimal(value) if value is not None else None

        header = {name: data.get(name) for name in cls.__dataclass_fields__ if name != "lines"}
        for name in ("subtotal", "cgst", "sgst", "igst", "total"):
            header[name] = amount(header[name])
        if header["invoice_date"]:
            header["invoice_date"] = date.fromisoformat(header["invoice_date"])
        text_fields = {"hsn_sac_code", "suggested_hsn_code"}
        lines = [
            LineData(
                **{
                    name: item.get(name) if name in text_fields else amount(item.get(name))
                    for name in LineData.__dataclass_fields__
                }
            )
            for item in data.get("line_items", [])
        ]
        return cls(**header, lines=lines)

    @classmethod
    def from_invoice(cls, invoice: Invoice) -> "InvoiceData":
        line_fields = LineData.__dataclass_fields__
        return cls(
            **{
                name: getattr(invoice, name) for name in cls.__dataclass_fields__ if name != "lines"
            },
            lines=[
                LineData(**{name: getattr(item, name) for name in line_fields})
                for item in invoice.line_items
            ],
        )


@dataclass(frozen=True)
class Issue:
    rule_code: str
    field: str
    severity: str  # "error" or "warning"
    message: str


def _rupees(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _percent(rate: Decimal) -> str:
    """Decimal('40.00') -> '40'. (normalize() alone prints 40 as '4E+1'.)"""
    return format(rate.normalize(), "f")


def _sum(values: list[Decimal | None]) -> Decimal:
    return sum((value for value in values if value is not None), ZERO)


def _off(expected: Decimal, actual: Decimal) -> bool:
    return abs(expected - actual) > TOLERANCE


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def check_required(inv: InvoiceData) -> list[Issue]:
    required = {
        "vendor_gstin": inv.vendor_gstin,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "total": inv.total,
    }
    return [
        Issue("REQUIRED", name, "error", f"{name.replace('_', ' ').capitalize()} is missing")
        for name, value in required.items()
        if value is None
    ]


def check_gstin_format(inv: InvoiceData) -> list[Issue]:
    issues = []
    for name in ("vendor_gstin", "buyer_gstin"):  # the buyer may be unregistered (no GSTIN)
        gstin = getattr(inv, name)
        if gstin and (problem := gstin_problem(gstin)):
            issues.append(Issue("GSTIN_FORMAT", name, "error", f"GSTIN {gstin} {problem}"))
    return issues


def check_state_code(inv: InvoiceData) -> list[Issue]:
    issues = []
    for name in ("vendor_gstin", "buyer_gstin"):
        gstin = getattr(inv, name)
        if gstin and gstin[:2].isdigit() and gstin[:2] not in STATE_CODES:
            issues.append(
                Issue("STATE_CODE", name, "error", f"{gstin[:2]} is not a GST state/UT code")
            )
    return issues


def _supply_states(inv: InvoiceData) -> tuple[str | None, str | None]:
    """(vendor state, place-of-supply state). Falls back to the buyer's GSTIN state."""
    vendor = inv.vendor_gstin[:2] if inv.vendor_gstin else None
    supply = state_code_of_place(inv.place_of_supply)
    if supply is None and inv.buyer_gstin:
        supply = inv.buyer_gstin[:2]
    vendor = vendor if vendor in STATE_CODES else None
    supply = supply if supply in STATE_CODES else None
    return vendor, supply


def check_tax_type(inv: InvoiceData) -> list[Issue]:
    vendor, supply = _supply_states(inv)
    if vendor is None or supply is None:
        if inv.place_of_supply and state_code_of_place(inv.place_of_supply) is None:
            return [
                Issue(
                    "TAX_TYPE",
                    "place_of_supply",
                    "warning",
                    f"Could not tell which state '{inv.place_of_supply}' is",
                )
            ]
        return []

    line_central = [line.cgst_amount for line in inv.lines] + [
        line.sgst_amount for line in inv.lines
    ]
    central_state = _sum([inv.cgst, inv.sgst, *line_central])
    integrated = _sum([inv.igst] + [line.igst_amount for line in inv.lines])
    if vendor == supply and integrated > 0:
        return [
            Issue(
                "TAX_TYPE",
                "igst",
                "error",
                f"IGST charged, but vendor and place of supply are both in "
                f"{STATE_CODES[vendor]}: expected CGST + SGST",
            )
        ]
    if vendor != supply and central_state > 0:
        return [
            Issue(
                "TAX_TYPE",
                "cgst",
                "error",
                f"CGST/SGST charged, but the supply goes from {STATE_CODES[vendor]} to "
                f"{STATE_CODES[supply]}: expected IGST",
            )
        ]
    return []


def check_line_math(inv: InvoiceData) -> list[Issue]:
    issues = []
    for number, line in enumerate(inv.lines, start=1):
        if None in (line.quantity, line.unit_price, line.taxable_value):
            continue
        expected = line.quantity * line.unit_price - (line.discount or ZERO)
        if _off(expected, line.taxable_value):
            issues.append(
                Issue(
                    "LINE_MATH",
                    f"line_items[{number}].taxable_value",
                    "error",
                    f"Line {number}: {line.quantity} × {_rupees(line.unit_price)}"
                    f"{f' − {_rupees(line.discount)}' if line.discount else ''} = "
                    f"{_rupees(expected)}, but taxable value is {_rupees(line.taxable_value)}",
                )
            )
    return issues


def check_tax_math(inv: InvoiceData) -> list[Issue]:
    issues = []
    lines_have_tax = any(
        value is not None
        for line in inv.lines
        for value in (line.cgst_amount, line.sgst_amount, line.igst_amount)
    )

    for number, line in enumerate(inv.lines, start=1):
        if line.cgst_amount is not None and line.sgst_amount is not None:
            if _off(line.cgst_amount, line.sgst_amount):
                issues.append(
                    Issue(
                        "TAX_MATH",
                        f"line_items[{number}].sgst_amount",
                        "error",
                        f"Line {number}: CGST {_rupees(line.cgst_amount)} and SGST "
                        f"{_rupees(line.sgst_amount)} should be equal",
                    )
                )
        if line.taxable_value is None or line.gst_rate is None:
            continue
        if line.igst_amount is None and line.cgst_amount is None and line.sgst_amount is None:
            continue  # this layout prints tax only at invoice level
        expected = line.taxable_value * line.gst_rate / 100
        actual = _sum([line.cgst_amount, line.sgst_amount, line.igst_amount])
        if _off(expected, actual):
            issues.append(
                Issue(
                    "TAX_MATH",
                    f"line_items[{number}].gst_rate",
                    "error",
                    f"Line {number}: {line.gst_rate}% of "
                    f"{_rupees(line.taxable_value)} is {_rupees(expected)}, "
                    f"but the tax shown is {_rupees(actual)}",
                )
            )

    if inv.cgst is not None and inv.sgst is not None and _off(inv.cgst, inv.sgst):
        issues.append(
            Issue(
                "TAX_MATH",
                "sgst",
                "error",
                f"Invoice CGST {_rupees(inv.cgst)} and SGST {_rupees(inv.sgst)} should be equal",
            )
        )

    invoice_tax = _sum([inv.cgst, inv.sgst, inv.igst])
    if lines_have_tax:
        line_tax = _sum(
            [
                v
                for line in inv.lines
                for v in (line.cgst_amount, line.sgst_amount, line.igst_amount)
            ]
        )
        if _off(line_tax, invoice_tax):
            issues.append(
                Issue(
                    "TAX_MATH",
                    "igst" if inv.igst else "cgst",
                    "error",
                    f"Line taxes add up to {_rupees(line_tax)}, but the invoice "
                    f"shows {_rupees(invoice_tax)} in tax",
                )
            )
    elif inv.lines and all(
        line.taxable_value is not None and line.gst_rate is not None for line in inv.lines
    ):
        expected = _sum([line.taxable_value * line.gst_rate / 100 for line in inv.lines])
        if _off(expected, invoice_tax):
            issues.append(
                Issue(
                    "TAX_MATH",
                    "igst" if inv.igst else "cgst",
                    "error",
                    f"Tax at the printed rates should be {_rupees(expected)}, "
                    f"but the invoice shows {_rupees(invoice_tax)}",
                )
            )
    return issues


def check_total_math(inv: InvoiceData) -> list[Issue]:
    issues = []
    taxables = [line.taxable_value for line in inv.lines]
    if inv.subtotal is not None and taxables and None not in taxables:
        if _off(_sum(taxables), inv.subtotal):
            issues.append(
                Issue(
                    "TOTAL_MATH",
                    "subtotal",
                    "error",
                    f"Line taxable values add up to {_rupees(_sum(taxables))}, "
                    f"but the subtotal is {_rupees(inv.subtotal)}",
                )
            )
    if inv.total is None:
        return issues

    line_totals = [line.line_total for line in inv.lines]
    if line_totals and None not in line_totals:
        expected, source = _sum(line_totals), "Line totals add up to"
    elif inv.subtotal is not None:
        expected = inv.subtotal + _sum([inv.cgst, inv.sgst, inv.igst])
        source = "Subtotal plus taxes is"
    else:
        return issues
    if _off(expected, inv.total):
        issues.append(
            Issue(
                "TOTAL_MATH",
                "total",
                "error",
                f"{source} {_rupees(expected)}, but the total is {_rupees(inv.total)}",
            )
        )
    return issues


def check_hsn(inv: InvoiceData) -> list[Issue]:
    """Compare what the invoice prints with the HSN/SAC lookup's suggestion (Phase 5).

    Warnings, not errors: the suggestion and its default rate are indicative, and
    a person decides. Codes are compared at heading level (first 4 digits).
    """
    issues = []
    for number, line in enumerate(inv.lines, start=1):
        printed = "".join(ch for ch in (line.hsn_sac_code or "") if ch.isdigit())
        suggested = line.suggested_hsn_code
        if printed and suggested and printed[:4] != suggested[:4]:
            issues.append(
                Issue(
                    "HSN_MISMATCH",
                    f"line_items[{number}].hsn_sac_code",
                    "warning",
                    f"Line {number}: printed code {line.hsn_sac_code} differs from the "
                    f"suggested code {suggested} (heading {suggested[:4]})",
                )
            )
        if (
            line.gst_rate is not None
            and line.suggested_gst_rate is not None
            and line.gst_rate != line.suggested_gst_rate
        ):
            issues.append(
                Issue(
                    "RATE_MISMATCH",
                    f"line_items[{number}].gst_rate",
                    "warning",
                    f"Line {number}: rate {_percent(line.gst_rate)}% differs from the default "
                    f"{_percent(line.suggested_gst_rate)}% for code {suggested} (verify: some "
                    "codes have conditional rates)",
                )
            )
    return issues


def check_date_range(inv: InvoiceData, today: date, max_age_days: int) -> list[Issue]:
    if inv.invoice_date is None:
        return []
    if inv.invoice_date > today:
        return [
            Issue(
                "DATE_RANGE",
                "invoice_date",
                "error",
                f"Invoice date {inv.invoice_date.isoformat()} is in the future",
            )
        ]
    if (today - inv.invoice_date).days > max_age_days:
        return [
            Issue(
                "DATE_RANGE",
                "invoice_date",
                "warning",
                f"Invoice date {inv.invoice_date.isoformat()} is more than {max_age_days} days old",
            )
        ]
    return []


def check_duplicate(same_file_ids: list[int], same_number_ids: list[int]) -> list[Issue]:
    """The caller looks up matching invoices; this rule only decides what to report."""
    issues = []
    if same_file_ids:
        issues.append(
            Issue(
                "DUPLICATE",
                "file",
                "error",
                f"The same file was already uploaded as invoice {same_file_ids[0]}",
            )
        )
    if same_number_ids:
        issues.append(
            Issue(
                "DUPLICATE",
                "invoice_number",
                "error",
                "The same vendor GSTIN and invoice number already exist on invoice "
                f"{same_number_ids[0]}",
            )
        )
    return issues


def validate(
    inv: InvoiceData,
    *,
    today: date,
    max_age_days: int,
    same_file_ids: list[int] | None = None,
    same_number_ids: list[int] | None = None,
) -> list[Issue]:
    """Run every rule. An empty list means the invoice is internally consistent."""
    return [
        *check_required(inv),
        *check_gstin_format(inv),
        *check_state_code(inv),
        *check_tax_type(inv),
        *check_line_math(inv),
        *check_tax_math(inv),
        *check_total_math(inv),
        *check_hsn(inv),
        *check_date_range(inv, today, max_age_days),
        *check_duplicate(same_file_ids or [], same_number_ids or []),
    ]


# ---------------------------------------------------------------------------
# Database glue
# ---------------------------------------------------------------------------


def save_validation(
    db: Session, invoice: Invoice, *, today: date, max_age_days: int
) -> list[Issue]:
    """Validate a saved invoice and replace its unresolved issues. The caller commits."""
    live = Invoice.status != InvoiceStatus.REJECTED  # rejected copies don't count
    same_file_ids = db.scalars(
        select(Invoice.id)
        .join(InvoiceFile, Invoice.file_id == InvoiceFile.id)
        .where(
            Invoice.user_id == invoice.user_id,
            Invoice.id != invoice.id,
            live,
            InvoiceFile.sha256 == invoice.file.sha256,
        )
        .order_by(Invoice.id)
    ).all()
    same_number_ids = []
    if invoice.vendor_gstin and invoice.invoice_number:
        same_number_ids = db.scalars(
            select(Invoice.id)
            .where(
                Invoice.user_id == invoice.user_id,
                Invoice.id != invoice.id,
                live,
                Invoice.vendor_gstin == invoice.vendor_gstin,
                Invoice.invoice_number == invoice.invoice_number,
            )
            .order_by(Invoice.id)
        ).all()

    issues = validate(
        InvoiceData.from_invoice(invoice),
        today=today,
        max_age_days=max_age_days,
        same_file_ids=list(same_file_ids),
        same_number_ids=list(same_number_ids),
    )
    # Rebuild the issue list from this run. A problem the reviewer dismissed stays
    # dismissed while it still applies; once it is fixed it simply disappears.
    dismissed = {
        (row.rule_code, row.field): row for row in invoice.validation_issues if row.resolved
    }
    rows = []
    for issue in issues:
        row = dismissed.pop((issue.rule_code, issue.field), None)
        if row is None:
            row = ValidationIssue(field=issue.field, rule_code=issue.rule_code)
        row.severity, row.message = issue.severity, issue.message
        rows.append(row)
    invoice.validation_issues = rows
    return issues
