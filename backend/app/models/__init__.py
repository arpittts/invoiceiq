"""Import every model here so Alembic and the app see the full schema."""

from app.models.extraction_run import ExtractionRun
from app.models.invoice import FileBlob, Invoice, InvoiceFile, InvoiceStatus, LineItem
from app.models.review_event import ReviewEvent
from app.models.user import User
from app.models.validation_issue import ValidationIssue

__all__ = [
    "ExtractionRun",
    "FileBlob",
    "Invoice",
    "InvoiceFile",
    "InvoiceStatus",
    "LineItem",
    "ReviewEvent",
    "User",
    "ValidationIssue",
]
