import hashlib

import pymupdf
from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import ExtractionRun, Invoice, InvoiceFile, InvoiceStatus
from app.schemas.invoice import ExtractionSummary, InvoiceDetailResponse, InvoiceUploadResponse
from app.services.extraction import process_invoice
from app.services.storage import save_file

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


@router.post("", response_model=InvoiceUploadResponse, status_code=status.HTTP_201_CREATED)
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
    db.commit()

    # Extraction takes seconds, so it runs after the response is sent.
    # The client polls GET /invoices/{id} until the status leaves "processing".
    background_tasks.add_task(process_invoice, invoice.id)
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
def get_invoice(invoice_id: int, user: CurrentUser, db: DbSession) -> InvoiceDetailResponse:
    invoice = db.get(Invoice, invoice_id)
    # Someone else's invoice gets the same 404 as a missing one, so ids can't be probed.
    if invoice is None or invoice.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")

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
    return response
