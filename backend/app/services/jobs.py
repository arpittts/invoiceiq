"""A small, durable job queue in Postgres for reading invoices.

Why: a FastAPI background task lives only in memory. A restart in the middle of
a read (every deploy, or the free host's sleep and out-of-memory restarts) left
the invoice "processing" forever, and a passing API error failed it on the
first try. Now every read is a row in `jobs`:

    queued ──claim──► running ──► done
       ▲                 │
       └── retry ◄───────┤ (transient error: back off, try again)
                         └──► failed (out of attempts: the invoice is marked failed)

- Claiming uses SELECT ... FOR UPDATE SKIP LOCKED and a lease (locked_until), so
  two runners never process the same job, and a job whose process died is taken
  again once its lease expires.
- Enqueue, then kick: the endpoint adds the job in the same transaction as the
  invoice and starts it straight away as a background task (fast, as before). A
  worker thread started with the app picks up anything else: retries, jobs
  whose server restarted, and work queued while the daily cap was reached.
- Reaching the daily AI cap is not a failure: the job waits until after
  midnight India time, without using up an attempt.

Everything runs inside the single free web service; no separate worker host.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Invoice, InvoiceStatus, Job, JobStatus

logger = logging.getLogger(__name__)

RETRY_DELAYS = [timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10)]
IST = ZoneInfo("Asia/Kolkata")
# Codes on the extraction attempts that mean "try again later", not "this invoice
# can't be read": network and API errors, and the daily cap.
TRANSIENT_CODES = {"API_ERROR", "DAILY_CAP"}


class Retry(Exception):
    """The job should run again later (not counted as a final failure)."""


class WaitForCap(Exception):
    """The daily AI cap is reached: run again after midnight IST."""


class Paused(Exception):
    """AI processing is switched off (EXTRACTION_ENABLED=false): check again later."""


@dataclass
class ClaimedJob:
    id: int
    invoice_id: int
    kind: str
    payload: dict | None
    attempts: int
    max_attempts: int


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


def enqueue(db: Session, invoice_id: int, kind: str = "read", payload: dict | None = None) -> Job:
    """Queue work for an invoice (in the caller's transaction; the caller commits).

    If the invoice already has an active job, that job is returned instead, so a
    double-click can't queue the same invoice twice.
    """
    active = db.scalar(
        select(Job).where(
            Job.invoice_id == invoice_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    )
    if active:
        return active
    job = Job(
        invoice_id=invoice_id,
        kind=kind,
        payload=payload,
        max_attempts=get_settings().job_max_attempts,
    )
    try:
        with db.begin_nested():  # the unique index is the final word under concurrency
            db.add(job)
            db.flush()
    except IntegrityError:
        return db.scalar(
            select(Job).where(
                Job.invoice_id == invoice_id,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
    return job


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

_CLAIM = """
UPDATE jobs SET status = 'running', attempts = attempts + 1,
       locked_until = now() + make_interval(secs => :lease)
WHERE id = (
    SELECT id FROM jobs
    WHERE {which}
      AND ((status = 'queued' AND run_after <= now())
           OR (status = 'running' AND locked_until < now()))
    ORDER BY run_after, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, invoice_id, kind, payload, attempts, max_attempts
"""


def claim(job_id: int | None = None) -> ClaimedJob | None:
    """Take the next due job (or this one), with a lease. None if nothing is due.

    A running job is only taken again once its lease has expired: its process died.
    """
    which = "id = :job_id" if job_id is not None else "TRUE"
    with engine.begin() as conn:
        row = conn.execute(
            text(_CLAIM.format(which=which)),
            {"lease": get_settings().job_lease_seconds, "job_id": job_id},
        ).first()
    return ClaimedJob(*row) if row else None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _execute(job: ClaimedJob) -> None:
    """Do the work. Raises Retry or WaitForCap for "later"; returns when finished."""
    from app.services import demo, extraction

    if not get_settings().extraction_enabled:
        raise Paused
    provider = None
    if job.kind == "demo":
        provider = demo.replay_provider((job.payload or {}).get("demo_file", ""))
    else:
        from app.services import budget

        if budget.usage_today().cap_reached:
            raise WaitForCap
    try:
        state = extraction.process_invoice(job.invoice_id, provider, raise_errors=True)
    except Exception as error:  # database blip, a crash inside the agent...
        raise Retry(f"{type(error).__name__}: {error}"[:500]) from error

    if not state or state.get("decision") != "failed":
        return  # read and checked (or nothing to do): finished
    codes = {issue.get("code") for attempt in state.get("attempts", []) for issue in attempt.issues}
    if codes and codes <= TRANSIENT_CODES:
        # Only network/API errors or the cap stopped this read: try again later.
        _back_to_processing(job.invoice_id)
        if "DAILY_CAP" in codes:
            raise WaitForCap
        raise Retry(state.get("reason") or "The AI service could not be reached")
    # Otherwise the model really couldn't read it (e.g. invalid output every time):
    # the invoice is already marked failed for a person; the job is done.


def _back_to_processing(invoice_id: int) -> None:
    with SessionLocal() as db:
        invoice = db.get(Invoice, invoice_id)
        if invoice is not None and invoice.status == InvoiceStatus.FAILED:
            invoice.status = InvoiceStatus.PROCESSING
            db.commit()


def next_midnight_ist(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(UTC)).astimezone(IST)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time(0, 5), tzinfo=IST)
    return tomorrow.astimezone(UTC)


def run(job: ClaimedJob) -> str:
    """Run one claimed job and record the outcome. Returns the job's new status."""
    finished = dict(locked_until=None)
    try:
        _execute(job)
    except Paused:
        _update(
            job.id,
            status=JobStatus.QUEUED,
            attempts=job.attempts - 1,
            run_after=datetime.now(UTC) + timedelta(minutes=10),
            last_error="AI processing is switched off on the server",
            **finished,
        )
        return JobStatus.QUEUED
    except WaitForCap:
        # Not the job's fault: give the attempt back and wait for the cap to reset.
        _update(
            job.id,
            status=JobStatus.QUEUED,
            attempts=job.attempts - 1,
            run_after=next_midnight_ist(),
            last_error="Waiting for the daily AI limit to reset (midnight India time)",
            **finished,
        )
        return JobStatus.QUEUED
    except Retry as error:
        if job.attempts < job.max_attempts:
            delay = RETRY_DELAYS[min(job.attempts - 1, len(RETRY_DELAYS) - 1)]
            _update(
                job.id,
                status=JobStatus.QUEUED,
                run_after=datetime.now(UTC) + delay,
                last_error=str(error),
                **finished,
            )
            logger.warning("Job %s: attempt %s failed, retrying: %s", job.id, job.attempts, error)
            return JobStatus.QUEUED
        _give_up(job, str(error))
        return JobStatus.FAILED
    except Exception as error:  # a bug in the queue itself: never leave the job hanging
        logger.exception("Job %s crashed", job.id)
        _give_up(job, f"{type(error).__name__}: {error}"[:500])
        return JobStatus.FAILED
    _update(job.id, status=JobStatus.DONE, finished_at=datetime.now(UTC), **finished)
    return JobStatus.DONE


def _give_up(job: ClaimedJob, reason: str) -> None:
    logger.error("Job %s failed after %s attempt(s): %s", job.id, job.attempts, reason)
    with SessionLocal() as db:
        row = db.get(Job, job.id)
        row.status, row.last_error = JobStatus.FAILED, reason
        row.locked_until, row.finished_at = None, datetime.now(UTC)
        invoice = db.get(Invoice, job.invoice_id)
        if invoice is not None and invoice.status == InvoiceStatus.PROCESSING:
            invoice.status = InvoiceStatus.FAILED
        db.commit()


def _update(job_id: int, **values) -> None:
    with SessionLocal() as db:
        row = db.get(Job, job_id)
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


def run_now(job_id: int) -> None:
    """The "kick" after a request: run this job right away unless someone else has it."""
    job = claim(job_id)
    if job is not None:
        run(job)


def run_pending(limit: int = 100) -> int:
    """Run due jobs until none are left (or `limit`). Returns how many ran."""
    count = 0
    while count < limit:
        job = claim()
        if job is None:
            break
        run(job)
        count += 1
    return count


def note_for(db: Session, invoice_id: int) -> str | None:
    """A sentence for the review page: why a read is waiting, or why it gave up."""
    job = db.scalar(
        select(Job).where(Job.invoice_id == invoice_id).order_by(Job.id.desc()).limit(1)
    )
    if job is None or not job.last_error or job.status == JobStatus.DONE:
        return None
    if job.status == JobStatus.FAILED:
        return f"Stopped after {job.attempts} attempt(s): {job.last_error}"
    when = job.run_after.astimezone(IST).strftime("%H:%M, %d %b")
    if job.last_error.startswith(("Waiting", "AI processing")):
        return f"{job.last_error}. Next try: {when} (India time)."
    return (
        f"Attempt {job.attempts} of {job.max_attempts} failed ({job.last_error}). "
        f"Trying again at {when} (India time)."
    )


# ---------------------------------------------------------------------------
# Recovery and the worker thread
# ---------------------------------------------------------------------------


def recover_orphans() -> int:
    """Invoices stuck in "processing" with no active job (e.g. from before the
    queue existed, or a server killed between commit and kick) get a job."""
    with SessionLocal() as db:
        active = select(Job.invoice_id).where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        stuck = db.scalars(
            select(Invoice.id).where(
                Invoice.status == InvoiceStatus.PROCESSING, Invoice.id.not_in(active)
            )
        ).all()
        for invoice_id in stuck:
            enqueue(db, invoice_id)
        db.commit()
    if stuck:
        logger.info("Queued %s invoice(s) left in processing: %s", len(stuck), list(stuck))
    return len(stuck)


class Worker:
    """Polls for due jobs in a daemon thread. One at a time (the free AI tier prefers it)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        try:
            recover_orphans()
        except Exception:
            logger.exception("Recovering stuck invoices failed")
        while not self._stop.is_set():
            ran = 0
            try:
                ran = run_pending(limit=5)
            except Exception:
                logger.exception("Job worker error")
            if not ran:
                self._wake.wait(get_settings().worker_poll_seconds)
                self._wake.clear()


worker = Worker()
