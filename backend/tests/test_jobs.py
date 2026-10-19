"""The job queue: reads survive restarts, retry passing errors, and give up cleanly."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.config import get_settings
from app.llm.provider import FakeProvider
from app.models import Invoice, InvoiceStatus, Job, JobStatus
from app.services import extraction, jobs
from tests.test_extraction_service import upload, valid_reply


class Down:
    """A provider whose API is down (every call raises)."""

    model = "fake-model"

    def generate_json(self, **kwargs):
        raise ConnectionError("503 Service Unavailable")


@pytest.fixture
def queued(client, auth_headers, db, monkeypatch):
    """Upload with processing switched off: the immediate run pauses the job, so it stays
    queued. Then switch processing on and make the job due for the test to run."""
    invoice_id = upload(client, auth_headers)
    monkeypatch.setattr(get_settings(), "extraction_enabled", True)
    make_due(db)
    return invoice_id


def job_of(db, invoice_id) -> Job:
    db.expire_all()
    return db.scalars(select(Job).where(Job.invoice_id == invoice_id).order_by(Job.id)).all()[-1]


def status_of(db, invoice_id) -> str:
    db.expire_all()
    return db.get(Invoice, invoice_id).status


def make_due(db, job_id=None):
    """Due now by the database's clock (the Docker VM's clock can lag the host's)."""
    query = update(Job).values(run_after=func.now() - timedelta(seconds=1))
    db.execute(query.where(Job.id == job_id) if job_id else query)
    db.commit()


# --- enqueue and claim -------------------------------------------------------------


def test_upload_queues_one_job_and_a_double_click_does_not_add_another(queued, db):
    first = job_of(db, queued)
    assert first.status == JobStatus.QUEUED  # processing was off at upload time
    again = jobs.enqueue(db, queued)
    db.commit()
    assert again.id == first.id
    assert len(db.scalars(select(Job).where(Job.invoice_id == queued)).all()) == 1


def test_two_runners_never_claim_the_same_job(queued, db):
    job_id = job_of(db, queued).id
    assert jobs.claim(job_id) is not None
    assert jobs.claim(job_id) is None  # held by the first runner's lease
    assert jobs.claim() is None


def test_a_job_whose_server_died_is_taken_again_after_its_lease(queued, db):
    job_id = jobs.claim().id  # claimed... and the process "dies" without finishing
    assert jobs.claim() is None
    db.execute(
        update(Job).where(Job.id == job_id).values(locked_until=func.now() - timedelta(seconds=1))
    )
    db.commit()
    retaken = jobs.claim()
    assert retaken.id == job_id and retaken.attempts == 2


# --- running ---------------------------------------------------------------------------


def test_a_queued_read_runs_and_finishes(queued, db, monkeypatch):
    monkeypatch.setattr(extraction, "get_provider", lambda: FakeProvider([valid_reply()]))
    assert jobs.run_pending() == 1
    assert job_of(db, queued).status == JobStatus.DONE
    assert status_of(db, queued) == InvoiceStatus.NEEDS_REVIEW


def test_a_passing_api_error_is_retried_with_backoff(queued, db, monkeypatch):
    monkeypatch.setattr(extraction, "get_provider", lambda: Down())
    jobs.run_pending()
    job = job_of(db, queued)
    assert job.status == JobStatus.QUEUED and job.attempts == 1
    assert job.run_after > datetime.now(UTC) + timedelta(seconds=20)  # backs off
    assert "503" in job.last_error
    assert status_of(db, queued) == InvoiceStatus.PROCESSING  # still in progress for the user

    # The API comes back before the next attempt: the read succeeds.
    monkeypatch.setattr(extraction, "get_provider", lambda: FakeProvider([valid_reply()]))
    make_due(db, job.id)
    jobs.run_pending()
    assert job_of(db, queued).status == JobStatus.DONE
    assert status_of(db, queued) == InvoiceStatus.NEEDS_REVIEW


def test_the_queue_gives_up_after_the_last_attempt(queued, db, monkeypatch):
    monkeypatch.setattr(extraction, "get_provider", lambda: Down())
    for _ in range(get_settings().job_max_attempts):
        make_due(db, job_of(db, queued).id)
        jobs.run_pending()
    job = job_of(db, queued)
    assert job.status == JobStatus.FAILED and job.attempts == 3 and job.finished_at
    assert status_of(db, queued) == InvoiceStatus.FAILED  # "Read again" can retry it


def test_an_unreadable_invoice_fails_without_wasting_retries(queued, db, monkeypatch):
    """Invalid output every time is the model's answer, not a passing error."""
    monkeypatch.setattr(extraction, "get_provider", lambda: FakeProvider(["not json"] * 3))
    jobs.run_pending()
    assert job_of(db, queued).status == JobStatus.DONE and job_of(db, queued).attempts == 1
    assert status_of(db, queued) == InvoiceStatus.FAILED


def test_a_crash_inside_processing_is_retried(queued, db, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(extraction, "process_invoice", crash)
    jobs.run_pending()
    job = job_of(db, queued)
    assert job.status == JobStatus.QUEUED and "connection lost" in job.last_error
    assert status_of(db, queued) == InvoiceStatus.PROCESSING


def test_the_daily_cap_defers_until_midnight_without_using_an_attempt(queued, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_llm_call_limit", 0)
    jobs.run_pending()
    job = job_of(db, queued)
    assert job.status == JobStatus.QUEUED and job.attempts == 0
    assert job.run_after == jobs.next_midnight_ist(job.run_after - timedelta(hours=1))
    assert "daily AI limit" in job.last_error


def test_switching_ai_off_pauses_jobs_instead_of_failing_them(queued, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_enabled", False)
    jobs.run_pending()
    job = job_of(db, queued)
    assert job.status == JobStatus.QUEUED and job.attempts == 0
    assert status_of(db, queued) == InvoiceStatus.PROCESSING


def test_next_midnight_is_in_india_time():
    # 20:00 UTC on 19 Oct is 01:30 IST on 20 Oct: the next reset is 21 Oct 00:05 IST.
    assert jobs.next_midnight_ist(datetime(2026, 10, 19, 20, 0, tzinfo=UTC)) == datetime(
        2026, 10, 20, 18, 35, tzinfo=UTC
    )


# --- recovery and the worker -----------------------------------------------------------


def test_invoices_left_in_processing_without_a_job_are_recovered(queued, db):
    db.execute(update(Job).values(status=JobStatus.DONE))  # as if from before the queue
    db.commit()
    assert jobs.recover_orphans() == 1
    assert job_of(db, queued).status == JobStatus.QUEUED
    assert jobs.recover_orphans() == 0  # idempotent


def test_the_worker_thread_picks_up_queued_work(queued, db, monkeypatch):
    monkeypatch.setattr(extraction, "get_provider", lambda: FakeProvider([valid_reply()]))
    monkeypatch.setattr(get_settings(), "worker_poll_seconds", 0.1)
    worker = jobs.Worker()
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while status_of(db, queued) == InvoiceStatus.PROCESSING and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        worker.stop()
    assert status_of(db, queued) == InvoiceStatus.NEEDS_REVIEW
    assert job_of(db, queued).status == JobStatus.DONE


def test_read_again_reuses_the_queue(client, auth_headers, db, queued, monkeypatch):
    monkeypatch.setattr(extraction, "get_provider", lambda: FakeProvider([valid_reply()] * 2))
    jobs.run_pending()
    response = client.post(f"/api/v1/invoices/{queued}/reprocess", headers=auth_headers)
    assert response.status_code == 200
    all_jobs = db.scalars(select(Job).where(Job.invoice_id == queued)).all()
    assert [j.status for j in all_jobs] == [JobStatus.DONE, JobStatus.DONE]
    assert status_of(db, queued) == InvoiceStatus.NEEDS_REVIEW
