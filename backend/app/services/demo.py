""" "Try demo invoices": load bundled sample invoices in one click (blueprint section 13).

The files in app/demo/ come with model replies recorded by the eval harness
(scripts/make_demo_bundle.py). Processing replays those replies while the
requests still match and only asks the real model for anything else (usually
the HSN step, whose candidates depend on the live catalogue). So the demo is
fast, uses almost no free-tier quota, and still reads invoices when the daily
cap is reached.
"""

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.replay import RecordingProvider
from app.models import Invoice, InvoiceFile, InvoiceStatus, User
from app.services import extraction
from app.services.storage import save_file

logger = logging.getLogger(__name__)

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


@lru_cache
def bundle() -> list[dict]:
    return json.loads((DEMO_DIR / "demo.json").read_text())["invoices"]


def _page_count(content: bytes, mime_type: str) -> int:
    if mime_type != "application/pdf":
        return 1
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        return doc.page_count


def create_demo_invoices(db: Session, user: User) -> tuple[list[tuple[int, int]], int]:
    """Add the demo invoices this user doesn't have yet, each with a queued job.

    Returns ([(invoice id, job id)], number skipped). This function commits, so the
    background run can see the rows.
    """
    from app.services import jobs

    created, skipped = [], 0
    for item in bundle():
        path = DEMO_DIR / item["file"]
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        already = db.scalar(
            select(Invoice.id)
            .join(InvoiceFile)
            .where(
                Invoice.user_id == user.id,
                InvoiceFile.sha256 == sha,
                Invoice.status != InvoiceStatus.REJECTED,
            )
        )
        if already:
            skipped += 1
            continue
        mime_type = MIME[path.suffix]
        file = InvoiceFile(
            user_id=user.id,
            original_name=f"demo-{item['file']}",
            mime_type=mime_type,
            storage_path="",
            page_count=_page_count(content, mime_type),
            sha256=sha,
        )
        db.add(file)
        db.flush()
        file.storage_path = save_file(db, file, content)
        invoice = Invoice(user_id=user.id, file=file, status=InvoiceStatus.PROCESSING)
        db.add(invoice)
        db.flush()
        job = jobs.enqueue(db, invoice.id, kind="demo", payload={"demo_file": item["file"]})
        created.append((invoice.id, job.id))
    db.commit()
    return created, skipped


class _NoModel:
    """Stands in when no API key is set: anything not recorded simply fails."""

    def generate_json(self, **kwargs):
        raise RuntimeError("No LLM API key is set; only recorded demo replies are available.")


def replay_provider(demo_file: str) -> RecordingProvider:
    """The provider for a demo job: recorded replies first, the real model after."""
    calls = next((item["calls"] for item in bundle() if item["file"] == demo_file), [])
    return RecordingProvider(extraction.get_provider() or _NoModel(), reuse=calls)
