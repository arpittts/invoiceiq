import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import Gstr2bStatement, Invoice
from app.ratelimit import limit_user
from app.services import gstr2b

router = APIRouter(prefix="/reconcile/gstr2b", tags=["reconcile"])

MAX_BYTES = 5 * 1024 * 1024
PeriodParam = Annotated[str, Path(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")]


class BookInvoice(BaseModel):
    id: int
    vendor_name: str | None
    vendor_gstin: str | None
    invoice_number: str | None
    invoice_date: date | None
    total: Decimal | None
    tax: Decimal


class ReconRow(BaseModel):
    id: int
    supplier_gstin: str
    supplier_name: str | None
    invoice_number: str
    invoice_date: date | None
    taxable_value: Decimal | None
    tax: Decimal
    invoice_value: Decimal | None
    itc_available: bool
    reverse_charge: bool
    match_status: str
    matched_invoice_id: int | None
    differences: list[dict] | None


class StatementSummary(BaseModel):
    period: str  # YYYY-MM
    file_name: str
    uploaded_at: datetime
    counts: dict[str, int]
    itc_claimable: Decimal
    itc_at_risk: Decimal


class ReconReport(StatementSummary):
    source_format: str
    verified_against_portal: bool
    rows: list[ReconRow]
    books_only: list[BookInvoice]
    still_waiting: list[BookInvoice]


def _tax(*amounts) -> Decimal:
    return sum((Decimal(a or 0) for a in amounts), Decimal(0))


def _book(inv: Invoice) -> BookInvoice:
    return BookInvoice(
        id=inv.id,
        vendor_name=inv.vendor_name,
        vendor_gstin=inv.vendor_gstin,
        invoice_number=inv.invoice_number,
        invoice_date=inv.invoice_date,
        total=inv.total,
        tax=_tax(inv.igst, inv.cgst, inv.sgst),
    )


def _summary(result: dict) -> dict:
    statement = result["statement"]
    return {
        "period": statement.period.strftime("%Y-%m"),
        "file_name": statement.file_name,
        "uploaded_at": statement.uploaded_at,
        "counts": result["counts"],
        "itc_claimable": result["itc_claimable"],
        "itc_at_risk": result["itc_at_risk"],
    }


def _report(db, statement: Gstr2bStatement) -> ReconReport:
    result = gstr2b.report(db, statement)
    return ReconReport(
        **_summary(result),
        source_format=statement.source_format,
        verified_against_portal=statement.source_format in gstr2b.VERIFIED_FORMATS,
        rows=[
            ReconRow(
                id=r.id,
                supplier_gstin=r.supplier_gstin,
                supplier_name=r.supplier_name,
                invoice_number=r.invoice_number,
                invoice_date=r.invoice_date,
                taxable_value=r.taxable_value,
                tax=_tax(r.igst, r.cgst, r.sgst),
                invoice_value=r.invoice_value,
                itc_available=r.itc_available,
                reverse_charge=r.reverse_charge,
                match_status=r.match_status,
                matched_invoice_id=r.matched_invoice_id,
                differences=r.differences,
            )
            for r in result["rows"]
        ],
        books_only=[_book(inv) for inv in result["books_only"]],
        still_waiting=[_book(inv) for inv in result["still_waiting"]],
    )


def _own_statement(db, user, period: str) -> Gstr2bStatement:
    first = date.fromisoformat(f"{period}-01")
    statement = db.scalar(
        select(Gstr2bStatement).where(
            Gstr2bStatement.user_id == user.id, Gstr2bStatement.period == first
        )
    )
    if statement is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No GSTR-2B uploaded for {period}")
    return statement


@router.post(
    "",
    response_model=ReconReport,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_user("reconcile")],
)
async def upload_gstr2b(file: UploadFile, user: CurrentUser, db: DbSession) -> ReconReport:
    """Upload a month's GSTR-2B, the portal's Excel (.xlsx) or JSON (replacing that month),
    and match it to your books."""
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is larger than 5 MB")
    try:
        parsed = gstr2b.parse_file(content, file.filename or "")
    except gstr2b.Gstr2bFormatError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not a GSTR-2B file we can read: {error}"
        ) from None
    statement = gstr2b.save_statement(db, user.id, parsed, file_name=file.filename or "gstr2b.json")
    db.commit()
    return _report(db, statement)


@router.post(
    "/sample/{period}",
    response_model=ReconReport,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_user("reconcile")],
)
def try_sample(period: PeriodParam, user: CurrentUser, db: DbSession) -> ReconReport:
    """Build a synthetic GSTR-2B from your own approved invoices for a month, and
    reconcile it: a way to try the page without a file from the GST portal."""
    first = date.fromisoformat(f"{period}-01")
    parsed = gstr2b.parse(gstr2b.sample_for(db, user.id, first))
    statement = gstr2b.save_statement(db, user.id, parsed, file_name="sample (synthetic)")
    statement.source_format = gstr2b.SAMPLE_FORMAT
    db.commit()
    return _report(db, statement)


@router.get("", response_model=list[StatementSummary])
def list_statements(user: CurrentUser, db: DbSession) -> list[dict]:
    statements = db.scalars(
        select(Gstr2bStatement)
        .where(Gstr2bStatement.user_id == user.id)
        .order_by(Gstr2bStatement.period.desc())
    ).all()
    return [_summary(gstr2b.report(db, s)) for s in statements]


@router.get("/{period}", response_model=ReconReport)
def get_report(period: PeriodParam, user: CurrentUser, db: DbSession) -> ReconReport:
    return _report(db, _own_statement(db, user, period))


@router.post("/{period}/recheck", response_model=ReconReport)
def recheck(period: PeriodParam, user: CurrentUser, db: DbSession) -> ReconReport:
    """Match again, e.g. after approving more invoices or correcting one."""
    statement = _own_statement(db, user, period)
    gstr2b.reconcile(db, statement)
    db.commit()
    return _report(db, statement)


@router.get("/{period}/export.csv", response_class=Response)
def export_report(period: PeriodParam, user: CurrentUser, db: DbSession) -> Response:
    """The month's reconciliation as CSV: every reported invoice, then books-only ones."""
    report = _report(db, _own_statement(db, user, period))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["result", "supplier_gstin", "supplier_name", "invoice_number", "invoice_date",
         "taxable_value", "tax", "invoice_value", "itc_available", "differences"]
    )  # fmt: skip
    for r in report.rows:
        diffs = "; ".join(
            f"{d['field']}: books {d['books']}, 2B {d['gstr2b']}" for d in r.differences or []
        )
        writer.writerow(
            [r.match_status, r.supplier_gstin, _safe(r.supplier_name), _safe(r.invoice_number),
             r.invoice_date or "", r.taxable_value or "", r.tax, r.invoice_value or "",
             "Y" if r.itc_available else "N", _safe(diffs)]
        )  # fmt: skip
    for label, invoices in (
        ("books_only", report.books_only),
        ("still_waiting", report.still_waiting),
    ):
        for inv in invoices:
            writer.writerow(
                [label, inv.vendor_gstin or "", _safe(inv.vendor_name), _safe(inv.invoice_number),
                 inv.invoice_date or "", "", inv.tax, inv.total or "", "", ""]
            )  # fmt: skip
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="gstr2b-reconcile-{period}.csv"'},
    )


def _safe(value) -> str:
    """Text from suppliers is untrusted: stop spreadsheets running it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
