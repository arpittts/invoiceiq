""" "Try the demo": a throwaway account, ready to explore, deleted after 24 hours.

A visitor gets a fresh user (no email, no usable password) holding:
- the ledger: 20 synthetic invoices, already approved, so the dashboard, CSV export,
  Ask and Reconcile have data straight away;
- a sample GSTR-2B for August 2026, reconciled, so the Reconcile page shows a report;
- 6 demo invoices to review, read by replaying recorded model replies (instant,
  almost no AI quota), including one with a misread GSTIN the checks catch.

Every file points at the copy shipped in app/demo/ (storage "bundle:"), so a demo
account costs a few database rows, not megabytes. Accounts are deleted 24 hours
after creation, and at most MAX_DEMO_ACCOUNTS exist at once (the oldest go first).
"""

import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache

import pymupdf
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceFile, InvoiceStatus, LineItem, User
from app.security import hash_password
from app.services import demo, gstr2b
from app.services.storage import BUNDLE_DIR, bundled

DEMO_TTL = timedelta(hours=24)
MAX_DEMO_ACCOUNTS = 300
SAMPLE_PERIOD = date(2026, 8, 1)  # a month of the sample ledger with 6 invoices
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}
LINE_AMOUNTS = (
    "quantity", "unit_price", "discount", "taxable_value", "gst_rate",
    "cgst_amount", "sgst_amount", "igst_amount", "line_total",
)  # fmt: skip


@lru_cache
def _ledger() -> list[dict]:
    """The bundled ledger, with each file's page count worked out once."""
    items = json.loads((BUNDLE_DIR / "ledger" / "ledger.json").read_text())
    for item in items:
        path = BUNDLE_DIR / item["file"]
        item["mime_type"] = MIME[path.suffix]
        if path.suffix == ".pdf":
            with pymupdf.open(path) as doc:
                item["page_count"] = doc.page_count
        else:
            item["page_count"] = 1
    return items


def _amount(value) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _add_ledger(db: Session, user: User) -> None:
    for n, item in enumerate(_ledger(), start=1):
        truth = item["truth"]
        db.add(
            Invoice(
                user_id=user.id,
                status=InvoiceStatus.APPROVED,
                approved_at=datetime.now(UTC),
                vendor_name=truth["vendor_name"],
                vendor_gstin=truth["vendor_gstin"],
                buyer_name=truth["buyer_name"],
                buyer_gstin=truth["buyer_gstin"],
                invoice_number=truth["invoice_number"],
                invoice_date=date.fromisoformat(truth["invoice_date"]),
                place_of_supply=truth["place_of_supply"],
                overall_confidence=1.0,
                **{k: _amount(truth[k]) for k in ("subtotal", "cgst", "sgst", "igst", "total")},
                file=InvoiceFile(
                    user_id=user.id,
                    original_name=f"sample-{n:02d}{BUNDLE_DIR.joinpath(item['file']).suffix}",
                    mime_type=item["mime_type"],
                    storage_path=bundled(item["file"]),
                    page_count=item["page_count"],
                    sha256=f"demo-ledger-{n}",
                ),
                line_items=[
                    LineItem(
                        position=position,
                        description=line["description"],
                        hsn_sac_code=line["hsn_sac_code"],
                        unit=line["unit"],
                        **{k: _amount(line[k]) for k in LINE_AMOUNTS},
                    )
                    for position, line in enumerate(truth["line_items"], start=1)
                ],
            )
        )


def expire_demo_accounts(db: Session, now: datetime | None = None) -> int:
    """Delete demo accounts older than DEMO_TTL (their data goes with them)."""
    cutoff = (now or datetime.now(UTC)) - DEMO_TTL
    result = db.execute(delete(User).where(User.is_demo, User.created_at < cutoff))
    db.commit()
    return result.rowcount or 0


def _make_room(db: Session) -> None:
    """Keep at most MAX_DEMO_ACCOUNTS - 1 before adding one: delete the oldest."""
    count = db.scalar(select(func.count()).select_from(User).where(User.is_demo)) or 0
    extra = count - (MAX_DEMO_ACCOUNTS - 1)
    if extra > 0:
        oldest = select(User.id).where(User.is_demo).order_by(User.created_at, User.id).limit(extra)
        db.execute(delete(User).where(User.id.in_(oldest)))
        db.flush()


def create_demo_account(db: Session) -> tuple[User, list[int]]:
    """Create and fill a demo account. Returns (user, ids of the queued read jobs)."""
    _make_room(db)
    user = User(
        email=f"demo-{uuid.uuid4().hex[:12]}@demo.invalid",
        password_hash=hash_password(secrets.token_urlsafe(32)),  # nobody knows it
        is_demo=True,
    )
    db.add(user)
    db.flush()
    _add_ledger(db, user)
    db.flush()
    sample = gstr2b.parse(gstr2b.sample_for(db, user.id, SAMPLE_PERIOD))
    statement = gstr2b.save_statement(db, user.id, sample, file_name="sample (synthetic)")
    statement.source_format = gstr2b.SAMPLE_FORMAT
    db.commit()
    created, _ = demo.create_demo_invoices(db, user)  # commits
    return user, [job_id for _, job_id in created]
