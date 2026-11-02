""" "Try the demo": a throwaway account that is ready to use and cleans itself up."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import FileBlob, Invoice, InvoiceFile, User
from app.ratelimit import PER_IP
from app.services import demo_account
from app.services.ledger_qa import run_readonly
from app.services.storage import load_file
from tests.conftest import register_and_login


def start_demo(client) -> dict:
    response = client.post("/api/v1/auth/demo")
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_try_the_demo_gives_a_ready_account(client, db):
    headers = start_demo(client)
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["is_demo"] is True and me["email"].endswith("@demo.invalid")

    dashboard = client.get("/api/v1/dashboard", headers=headers).json()
    assert dashboard["counts"]["approved"] == 20  # the ledger
    # The 6 invoices to review are queued (processing is off in tests).
    assert dashboard["counts"]["processing"] + dashboard["counts"]["needs_review"] == 6

    # Ask has a ledger to query, isolated to this account.
    assert run_readonly("SELECT COUNT(*) FROM v_invoices", me["id"])[1] == [[20]]

    # Reconcile already has a report.
    report = client.get("/api/v1/reconcile/gstr2b/2026-08", headers=headers).json()
    assert report["source_format"].startswith("synthetic sample")
    assert report["counts"]["matched"] >= 1 and report["counts"]["books_only"] == 1


def test_demo_files_are_shared_not_copied(client, db):
    headers = start_demo(client)
    files = db.scalars(select(InvoiceFile)).all()
    assert files and all(f.storage_path.startswith("bundle:") for f in files)
    assert db.scalar(select(func.count()).select_from(FileBlob)) == 0
    invoice_id = db.scalar(select(Invoice.id).where(Invoice.status == "approved").limit(1))
    page = client.get(f"/api/v1/invoices/{invoice_id}/pages/1", headers=headers)
    assert page.status_code == 200 and page.headers["content-type"].startswith("image/")


def test_bundle_storage_never_reads_outside_the_bundle(db):
    sneaky = InvoiceFile(storage_path="bundle:../../app/config.py")
    with pytest.raises(FileNotFoundError):
        load_file(db, sneaky)


def test_every_visitor_gets_their_own_account(client):
    first = client.get("/api/v1/auth/me", headers=start_demo(client)).json()
    second = client.get("/api/v1/auth/me", headers=start_demo(client)).json()
    assert first["id"] != second["id"]


def test_nobody_can_sign_in_to_a_demo_account(client, db):
    me = client.get("/api/v1/auth/me", headers=start_demo(client)).json()
    response = client.post(
        "/api/v1/auth/login", json={"email": me["email"], "password": "anything-at-all"}
    )
    assert response.status_code in (401, 422)


def test_demo_accounts_are_limited_per_ip(client):
    limit, _ = PER_IP["demo_account"]
    for _ in range(limit):
        start_demo(client)
    assert client.post("/api/v1/auth/demo").status_code == 429


def test_demo_accounts_are_deleted_after_24_hours(client, db):
    start_demo(client)
    register_and_login(client, "real@example.com")  # a real account must survive
    db.execute(
        update(User).where(User.is_demo).values(created_at=datetime.now(UTC) - timedelta(hours=25))
    )
    db.commit()
    assert demo_account.expire_demo_accounts(db) == 1
    assert db.scalars(select(User.email)).all() == ["real@example.com"]
    assert db.scalar(select(func.count()).select_from(Invoice)) == 0  # data went with it


def test_a_fresh_demo_account_is_kept(client, db):
    start_demo(client)
    assert demo_account.expire_demo_accounts(db) == 0


def test_the_oldest_demo_accounts_make_room(client, db, monkeypatch):
    monkeypatch.setattr(demo_account, "MAX_DEMO_ACCOUNTS", 2)
    ids = [client.get("/api/v1/auth/me", headers=start_demo(client)).json()["id"] for _ in range(3)]
    remaining = db.scalars(select(User.id).where(User.is_demo).order_by(User.id)).all()
    assert remaining == ids[1:]
