import csv
import hashlib
import io
from datetime import date
from decimal import Decimal
from typing import Annotated

import pymupdf
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import ExtractionRun, Invoice, InvoiceFile, InvoiceStatus, ReviewEvent
from app.ratelimit import limit_user
from app.schemas.invoice import (
    DemoResponse,
    ExtractionSummary,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceSummary,
    InvoiceUpdate,
    InvoiceUploadResponse,
)
from app.services import budget, demo, jobs, review
from app.services.pdf import render_pages
from app.services.storage import load_file, save_file
from app.services.validation import india_today

router = APIRouter(prefix="/invoices", tags=["invoices"])

# The first bytes ("magic numbers") of each allowed file type. We trust these,
# not the filename or the Content-Type header, which the client controls.
MAGIC_NUMBERS = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def detect_mime_type(content: bytes) -> str | None:
    for magic, mime_type in MAGIC_NUMBERS.items():
        if content.startswith(magic):
            return mime_type
    return None


def count_pages(content: bytes, mime_type: str) -> int:
    if mime_type != "application/pdf":
        return 1
    try:
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The PDF could not be read") from None


def _ensure_ai_available() -> None:
    """Refuse new AI work once today's cap is reached (the demo invoices still work)."""
    try:
        budget.ensure_available()
    except budget.DailyCapReached as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None


@router.post(
    "",
    response_model=InvoiceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_user("upload")],
)
async def upload_invoice(
    file: UploadFile, user: CurrentUser, db: DbSession, background_tasks: BackgroundTasks
) -> Invoice:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    # Read at most one byte past the limit, so a huge upload can't fill memory.
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File is larger than {settings.max_upload_mb} MB"
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    mime_type = detect_mime_type(content)
    if mime_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF, PNG and JPEG files are accepted"
        )

    page_count = count_pages(content, mime_type)
    if page_count == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The PDF has no pages")
    if page_count > settings.max_pages:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"PDF has more than {settings.max_pages} pages"
        )

    _ensure_ai_available()

    invoice_file = InvoiceFile(
        user_id=user.id,
        original_name=(file.filename or "upload")[:255],
        mime_type=mime_type,
        storage_path="",  # filled in below, once the row has an id
        page_count=page_count,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    db.add(invoice_file)
    db.flush()  # assigns invoice_file.id without committing yet
    invoice_file.storage_path = save_file(db, invoice_file, content)

    invoice = Invoice(user_id=user.id, file=invoice_file, status=InvoiceStatus.PROCESSING)
    db.add(invoice)
    db.flush()
    job = jobs.enqueue(db, invoice.id)  # durable: survives a restart before it runs
    db.commit()

    # Reading takes seconds, so it starts after the response is sent; if the server
    # restarts first, the queue's worker picks the job up. The client polls
    # GET /invoices/{id} until the status leaves "processing".
    background_tasks.add_task(jobs.run_now, job.id)
    return invoice


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    vendor: Annotated[str | None, Query(max_length=100)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvoiceListResponse:
    """Your invoices, newest first, with optional filters and paging."""
    query = select(Invoice).where(Invoice.user_id == user.id)
    if status_filter:
        query = query.where(Invoice.status == status_filter)
    if vendor:
        query = query.where(Invoice.vendor_name.ilike(f"%{vendor}%"))
    if date_from:
        query = query.where(Invoice.invoice_date >= date_from)
    if date_to:
        query = query.where(Invoice.invoice_date <= date_to)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    invoices = db.scalars(
        query.options(selectinload(Invoice.file), selectinload(Invoice.validation_issues))
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return InvoiceListResponse(items=[summarise(inv) for inv in invoices], total=total)


def summarise(inv: Invoice) -> InvoiceSummary:
    """One list row (also used by the dashboard's recent invoices)."""
    return InvoiceSummary(
        id=inv.id,
        status=inv.status,
        created_at=inv.created_at,
        original_name=inv.file.original_name,
        vendor_name=inv.vendor_name,
        invoice_number=inv.invoice_number,
        invoice_date=inv.invoice_date,
        total=inv.total,
        overall_confidence=inv.overall_confidence,
        open_errors=sum(i.severity == "error" and not i.resolved for i in inv.validation_issues),
        open_warnings=sum(
            i.severity == "warning" and not i.resolved for i in inv.validation_issues
        ),
    )


@router.post("/demo", response_model=DemoResponse, dependencies=[limit_user("demo")])
def load_demo_invoices(
    user: CurrentUser, db: DbSession, background_tasks: BackgroundTasks
) -> DemoResponse:
    """Add the bundled demo invoices to your account and read them (replayed, so instant)."""
    created, skipped = demo.create_demo_invoices(db, user)
    for _, job_id in created:
        background_tasks.add_task(jobs.run_now, job_id)
    return DemoResponse(created=len(created), skipped=skipped, invoice_ids=[i for i, _ in created])


EXPORT_COLUMNS = [
    ("invoice_id", "id"), ("status", "status"), ("vendor_name", "vendor_name"),
    ("vendor_gstin", "vendor_gstin"), ("buyer_name", "buyer_name"),
    ("buyer_gstin", "buyer_gstin"), ("invoice_number", "invoice_number"),
    ("invoice_date", "invoice_date"), ("place_of_supply", "place_of_supply"),
    ("taxable_amount", "subtotal"), ("cgst", "cgst"), ("sgst", "sgst"), ("igst", "igst"),
    ("total", "total"), ("currency", "currency"), ("approved_at", "approved_at"),
]  # fmt: skip


def _csv_cell(value) -> str:
    """Text from an invoice is untrusted: stop spreadsheets running it as a formula."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, str) and text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/export.csv", response_class=Response)
def export_csv(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = InvoiceStatus.APPROVED,
) -> Response:
    """Your invoices as CSV (approved ones by default), oldest invoice date first."""
    query = select(Invoice).where(Invoice.user_id == user.id)
    if status_filter:
        query = query.where(Invoice.status == status_filter)
    invoices = db.scalars(query.order_by(Invoice.invoice_date, Invoice.id)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(name for name, _ in EXPORT_COLUMNS)
    for invoice in invoices:
        writer.writerow(_csv_cell(getattr(invoice, attr)) for _, attr in EXPORT_COLUMNS)
    name = f"invoiceiq-{status_filter or 'all'}-{india_today().isoformat()}.csv"
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _get_own_invoice(db, user, invoice_id: int) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    # Someone else's invoice gets the same 404 as a missing one, so ids can't be probed.
    if invoice is None or invoice.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return invoice


def _field_confidence(runs: list[ExtractionRun]) -> dict[str, float]:
    """Per-field confidence from the last accepted extraction attempt."""
    accepted = [run for run in runs if run.raw_json and not run.issues_json]
    if not accepted:
        return {}
    raw = accepted[-1].raw_json
    scores = {}
    for name, value in raw.items():
        if name == "line_items":
            for position, item in enumerate(value, start=1):
                for field, printed in item.items():
                    scores[f"line_items[{position}].{field}"] = printed["confidence"]
        elif isinstance(value, dict) and "confidence" in value:
            scores[name] = value["confidence"]
    return scores


def _detail(db, invoice: Invoice) -> InvoiceDetailResponse:
    runs = db.scalars(
        select(ExtractionRun)
        .where(ExtractionRun.invoice_id == invoice.id)
        .order_by(ExtractionRun.id)
    ).all()
    response = InvoiceDetailResponse.model_validate(invoice)
    if runs:
        costs = [run.cost_usd for run in runs]
        response.extraction = ExtractionSummary(
            attempts=len(runs),
            total_cost_usd=None if None in costs else sum(costs),
            total_latency_ms=sum(run.latency_ms for run in runs),
            issues=runs[-1].issues_json,
        )
        response.field_confidence = _field_confidence(list(runs))
    response.queue_note = jobs.note_for(db, invoice.id)
    return response


@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
def get_invoice(invoice_id: int, user: CurrentUser, db: DbSession) -> InvoiceDetailResponse:
    return _detail(db, _get_own_invoice(db, user, invoice_id))


@router.get(
    "/{invoice_id}/pages/{page}",
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}},
    response_class=Response,
)
def get_page_image(invoice_id: int, page: int, user: CurrentUser, db: DbSession) -> Response:
    """One page of the invoice as an image, for the review screen's viewer."""
    invoice = _get_own_invoice(db, user, invoice_id)
    if not 1 <= page <= invoice.file.page_count:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such page")
    content = load_file(db, invoice.file)
    image = render_pages(content, invoice.file.mime_type, max_edge=1600)[page - 1]
    # Private: only this user's browser may cache it.
    return Response(
        image.data, media_type=image.media_type, headers={"Cache-Control": "private, max-age=3600"}
    )


def _run_review(db, action) -> None:
    """Run a review action; turn rule violations into HTTP errors."""
    try:
        action()
    except review.ReviewError as error:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    except LookupError as error:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from None
    db.commit()


@router.patch("/{invoice_id}", response_model=InvoiceDetailResponse)
def update_invoice(
    invoice_id: int, body: InvoiceUpdate, user: CurrentUser, db: DbSession
) -> InvoiceDetailResponse:
    """Save reviewer corrections, log them, and re-run validation."""
    invoice = _get_own_invoice(db, user, invoice_id)
    _run_review(db, lambda: review.apply_update(db, invoice, user, body))
    return _detail(db, invoice)


@router.post("/{invoice_id}/issues/{issue_id}/dismiss", response_model=InvoiceDetailResponse)
def dismiss_issue(
    invoice_id: int, issue_id: int, user: CurrentUser, db: DbSession
) -> InvoiceDetailResponse:
    invoice = _get_own_invoice(db, user, invoice_id)
    _run_review(db, lambda: review.dismiss_issue(db, invoice, user, issue_id))
    return _detail(db, invoice)


@router.post("/{invoice_id}/approve", response_model=InvoiceDetailResponse)
def approve_invoice(invoice_id: int, user: CurrentUser, db: DbSession) -> InvoiceDetailResponse:
    invoice = _get_own_invoice(db, user, invoice_id)
    _run_review(db, lambda: review.approve(db, invoice, user))
    return _detail(db, invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceDetailResponse)
def reject_invoice(invoice_id: int, user: CurrentUser, db: DbSession) -> InvoiceDetailResponse:
    invoice = _get_own_invoice(db, user, invoice_id)
    _run_review(db, lambda: review.reject(db, invoice, user))
    return _detail(db, invoice)


@router.post("/{invoice_id}/reopen", response_model=InvoiceDetailResponse)
def reopen_invoice(invoice_id: int, user: CurrentUser, db: DbSession) -> InvoiceDetailResponse:
    """Move an approved or rejected invoice back to review (it leaves the ledger)."""
    invoice = _get_own_invoice(db, user, invoice_id)
    _run_review(db, lambda: review.reopen(db, invoice, user))
    return _detail(db, invoice)


@router.post(
    "/{invoice_id}/reprocess",
    response_model=InvoiceDetailResponse,
    dependencies=[limit_user("reprocess")],
)
def reprocess_invoice(
    invoice_id: int, user: CurrentUser, db: DbSession, background_tasks: BackgroundTasks
) -> InvoiceDetailResponse:
    """Read a failed or unreviewed invoice again from scratch (human edits are replaced)."""
    invoice = _get_own_invoice(db, user, invoice_id)
    if invoice.status not in (InvoiceStatus.FAILED, InvoiceStatus.NEEDS_REVIEW):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A {invoice.status} invoice can't be read again"
        )
    _ensure_ai_available()
    db.add(
        ReviewEvent(
            invoice_id=invoice.id,
            user_id=user.id,
            field="status",
            old_value=invoice.status,
            new_value="reprocess",
        )
    )
    invoice.status = InvoiceStatus.PROCESSING
    job = jobs.enqueue(db, invoice.id)
    db.commit()
    background_tasks.add_task(jobs.run_now, job.id)
    return _detail(db, invoice)
