"""Human review: corrections, dismissing issues, approving and rejecting.

Every change is written to review_events (who changed what, from what, to what),
and corrections re-run validation so fixed problems disappear right away.
Kept separate from FastAPI so the rules can be tested directly.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.embeddings import get_embedder
from app.models import Invoice, InvoiceStatus, LineItem, ReviewEvent, User
from app.schemas.invoice import InvoiceUpdate
from app.services.classification import classify_invoice
from app.services.extraction import get_provider
from app.services.validation import india_today, save_validation

EDITABLE = {InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.FAILED}
LINE_FIELDS = [
    "description",
    "hsn_sac_code",
    "quantity",
    "unit",
    "unit_price",
    "discount",
    "taxable_value",
    "gst_rate",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "line_total",
]


SUGGESTION_FIELDS = [
    "hsn_source_id",
    "suggested_hsn_code",
    "suggested_gst_rate",
    "hsn_confidence",
    "hsn_reason",
]


class ReviewError(Exception):
    """An action that isn't allowed in the invoice's current state (becomes HTTP 409)."""


def _text(value) -> str | None:
    """How a value is stored in review_events."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _same(old, new) -> bool:
    if isinstance(old, Decimal) and isinstance(new, Decimal):
        return old == new  # 100.00 == 100
    return old == new


def _event(db: Session, invoice: Invoice, user: User, field: str, old, new) -> None:
    db.add(
        ReviewEvent(
            invoice_id=invoice.id,
            user_id=user.id,
            field=field,
            old_value=_text(old),
            new_value=_text(new),
        )
    )


def _classify_new_lines(db: Session, invoice: Invoice) -> None:
    """Suggest codes for lines that were added or re-described (one LLM call, if any)."""
    provider = get_provider()
    if provider is None or not get_settings().classification_enabled:
        return
    db.flush()
    classify_invoice(db, invoice, provider, get_embedder(), only_unclassified=True)


def _revalidate(db: Session, invoice: Invoice) -> None:
    save_validation(
        db, invoice, today=india_today(), max_age_days=get_settings().max_invoice_age_days
    )


def apply_update(db: Session, invoice: Invoice, user: User, update: InvoiceUpdate) -> int:
    """Apply reviewer corrections. Returns how many values changed. Caller commits."""
    if invoice.status not in EDITABLE:
        raise ReviewError(f"An invoice that is {invoice.status} can't be edited")

    changes = 0
    sent = update.model_dump(exclude_unset=True)  # only the fields the client sent
    for name, new in sent.items():
        if name == "line_items":
            continue
        if isinstance(new, str):
            new = " ".join(new.split()) or None  # GSTINs are already cleaned by the schema
        old = getattr(invoice, name)
        if not _same(old, new):
            _event(db, invoice, user, name, old, new)
            setattr(invoice, name, new)
            changes += 1

    if update.line_items is not None:
        line_changes = _update_lines(db, invoice, user, update)
        changes += line_changes
        if line_changes:
            _classify_new_lines(db, invoice)

    if changes:
        if invoice.status == InvoiceStatus.FAILED:
            _event(db, invoice, user, "status", invoice.status, InvoiceStatus.NEEDS_REVIEW)
            invoice.status = InvoiceStatus.NEEDS_REVIEW  # entered by hand: ready for review
        _revalidate(db, invoice)
    return changes


def _update_lines(db: Session, invoice: Invoice, user: User, update: InvoiceUpdate) -> int:
    old_lines = list(invoice.line_items)
    new_lines = update.line_items or []
    changes = 0
    rows = []
    for index, new in enumerate(new_lines):
        position = index + 1
        old = old_lines[index] if index < len(old_lines) else None
        edited = old is None
        if old is None:
            _event(db, invoice, user, f"line_items[{position}]", None, "added")
            changes += 1
        else:
            for name in LINE_FIELDS:
                if not _same(getattr(old, name), getattr(new, name)):
                    _event(
                        db,
                        invoice,
                        user,
                        f"line_items[{position}].{name}",
                        getattr(old, name),
                        getattr(new, name),
                    )
                    changes += 1
                    edited = True
        row = LineItem(
            position=position,
            **{name: getattr(new, name) for name in LINE_FIELDS},
            # The model's confidence no longer applies once a human edits the row.
            confidence=None if edited else old.confidence,
        )
        if old is not None and (old.description, old.hsn_sac_code) == (
            new.description,
            new.hsn_sac_code,
        ):
            # Same item: keep its HSN/SAC suggestion instead of asking the LLM again.
            for name in SUGGESTION_FIELDS:
                setattr(row, name, getattr(old, name))
        rows.append(row)
    for position in range(len(new_lines) + 1, len(old_lines) + 1):
        _event(db, invoice, user, f"line_items[{position}]", "present", "removed")
        changes += 1
    if changes:
        invoice.line_items = rows
    return changes


def dismiss_issue(db: Session, invoice: Invoice, user: User, issue_id: int) -> None:
    """The reviewer accepts a flagged problem as fine (e.g. a known vendor quirk)."""
    if invoice.status != InvoiceStatus.NEEDS_REVIEW:
        raise ReviewError("Only invoices waiting for review have issues to dismiss")
    issue = next((i for i in invoice.validation_issues if i.id == issue_id), None)
    if issue is None:
        raise LookupError("Issue not found")
    if not issue.resolved:
        issue.resolved = True
        _event(db, invoice, user, f"issue:{issue.rule_code}:{issue.field}", "open", "dismissed")


def approve(db: Session, invoice: Invoice, user: User) -> None:
    if invoice.status != InvoiceStatus.NEEDS_REVIEW:
        raise ReviewError(f"An invoice that is {invoice.status} can't be approved")
    open_errors = [i for i in invoice.validation_issues if i.severity == "error" and not i.resolved]
    if open_errors:
        raise ReviewError(
            f"{len(open_errors)} error(s) must be fixed or dismissed first: "
            + ", ".join(sorted({i.rule_code for i in open_errors}))
        )
    _event(db, invoice, user, "status", invoice.status, InvoiceStatus.APPROVED)
    invoice.status = InvoiceStatus.APPROVED
    invoice.approved_at = datetime.now(UTC)
    try:
        db.flush()  # the approved-only unique index checks the ledger here
    except IntegrityError:
        db.rollback()
        raise ReviewError(
            "Another approved invoice already has this vendor GSTIN and invoice number"
        ) from None


def reject(db: Session, invoice: Invoice, user: User) -> None:
    if invoice.status not in EDITABLE:
        raise ReviewError(f"An invoice that is {invoice.status} can't be rejected")
    _event(db, invoice, user, "status", invoice.status, InvoiceStatus.REJECTED)
    invoice.status = InvoiceStatus.REJECTED
