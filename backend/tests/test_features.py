"""Phase 9 features: demo invoices, reprocess, CSV export, dashboard and settings."""

import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import ExtractionRun, Invoice, InvoiceStatus
from app.services import demo
from tests.conftest import register_and_login

TRUTH = Path(__file__).resolve().parents[2] / "evals/datasets/test"


def add_invoice(db, user_id, **fields):
    from app.models import InvoiceFile

    file = InvoiceFile(
        user_id=user_id,
        original_name="x.pdf",
        mime_type="application/pdf",
        storage_path="x",
        page_count=1,
        sha256=hashlib.sha256(f"{user_id}-{fields}".encode()).hexdigest(),
    )
    invoice = Invoice(user_id=user_id, file=file, **fields)
    db.add(invoice)
    db.commit()
    return invoice


def me(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["id"]


# --- demo invoices ----------------------------------------------------------------


def test_demo_invoices_are_read_from_recorded_replies(client, auth_headers, db, extraction_on):
    """No model at all (the FakeProvider has no replies): every read comes from the bundle."""
    body = client.post("/api/v1/invoices/demo", headers=auth_headers).json()
    assert body["created"] == len(demo.bundle()) == 6 and body["skipped"] == 0

    invoices = {
        inv.file.original_name: inv
        for inv in db.scalars(select(Invoice).where(Invoice.id.in_(body["invoice_ids"])))
    }
    assert all(inv.status == InvoiceStatus.NEEDS_REVIEW for inv in invoices.values())
    clean = invoices["demo-test_021.pdf"]
    truth = json.loads((TRUTH / "test_021.json").read_text())
    assert clean.vendor_gstin == truth["vendor_gstin"]
    assert clean.total == Decimal(truth["total"])
    assert len(clean.line_items) == len(truth["line_items"])
    # The blurred photo's misread GSTIN is caught, exactly as in the recorded eval run.
    flagged = invoices["demo-test_044.jpg"]
    assert "GSTIN_FORMAT" in {i.rule_code for i in flagged.validation_issues}
    assert extraction_on.calls == []  # nothing reached the (fake) model


def test_loading_the_demo_twice_adds_nothing_new(client, auth_headers, extraction_on):
    client.post("/api/v1/invoices/demo", headers=auth_headers)
    again = client.post("/api/v1/invoices/demo", headers=auth_headers).json()
    assert again["created"] == 0 and again["skipped"] == 6


def test_demo_works_when_the_daily_cap_is_reached(client, auth_headers, extraction_on, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "daily_llm_call_limit", 0)
    body = client.post("/api/v1/invoices/demo", headers=auth_headers).json()
    assert body["created"] == 6


# --- reprocess --------------------------------------------------------------------


def test_reprocess_reads_a_failed_invoice_again(client, auth_headers, db):
    invoice = add_invoice(db, me(client, auth_headers), status=InvoiceStatus.FAILED)
    response = client.post(f"/api/v1/invoices/{invoice.id}/reprocess", headers=auth_headers)
    assert response.status_code == 200
    # Extraction is off in tests, so the background task leaves it in processing.
    assert response.json()["status"] == "processing"


def test_approved_invoices_cannot_be_reprocessed(client, auth_headers, db):
    invoice = add_invoice(db, me(client, auth_headers), status=InvoiceStatus.APPROVED)
    response = client.post(f"/api/v1/invoices/{invoice.id}/reprocess", headers=auth_headers)
    assert response.status_code == 409


def test_someone_elses_invoice_cannot_be_reprocessed(client, db):
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    invoice = add_invoice(db, me(client, alice), status=InvoiceStatus.FAILED)
    assert client.post(f"/api/v1/invoices/{invoice.id}/reprocess", headers=bob).status_code == 404


# --- CSV export -------------------------------------------------------------------


def test_export_lists_approved_invoices_and_defuses_formulas(client, auth_headers, db):
    user_id = me(client, auth_headers)
    add_invoice(
        db,
        user_id,
        status=InvoiceStatus.APPROVED,
        vendor_name='=HYPERLINK("http://evil")',
        invoice_number="A-1",
        total=Decimal("1180.00"),
    )
    add_invoice(db, user_id, status=InvoiceStatus.NEEDS_REVIEW, invoice_number="B-2")
    response = client.get("/api/v1/invoices/export.csv", headers=auth_headers)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["invoice_number"] for r in rows] == ["A-1"]  # approved only
    assert rows[0]["vendor_name"].startswith("'=")  # not a live formula
    assert rows[0]["total"] == "1180.00"


def test_export_never_includes_other_users(client, db):
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    add_invoice(db, me(client, alice), status=InvoiceStatus.APPROVED, invoice_number="ALICE")
    text = client.get("/api/v1/invoices/export.csv", headers=bob).text
    assert "ALICE" not in text


# --- dashboard --------------------------------------------------------------------


def test_dashboard_counts_and_totals(client, auth_headers, db):
    user_id = me(client, auth_headers)
    approved = add_invoice(
        db,
        user_id,
        status=InvoiceStatus.APPROVED,
        total=Decimal("1180.00"),
        igst=Decimal("180.00"),
    )
    add_invoice(db, user_id, status=InvoiceStatus.NEEDS_REVIEW, total=Decimal("999.00"))
    db.add(
        ExtractionRun(
            invoice_id=approved.id,
            attempt=1,
            model="m",
            issues_json=[],
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("0.002"),
            latency_ms=1,
        )
    )
    db.commit()
    body = client.get("/api/v1/dashboard", headers=auth_headers).json()
    assert body["counts"]["approved"] == 1 and body["counts"]["needs_review"] == 1
    assert Decimal(body["approved_total"]) == Decimal("1180.00")
    assert Decimal(body["approved_gst"]) == Decimal("180.00")
    assert len(body["recent"]) == 2
    assert body["ai"]["extraction_calls"] == 1 and Decimal(body["ai"]["cost_usd"]) > 0
    assert body["ai"]["cap_reached"] is False


# --- settings ---------------------------------------------------------------------


def test_settings_default_to_the_server_and_can_be_changed(client, auth_headers):
    first = client.get("/api/v1/settings", headers=auth_headers).json()
    assert first["auto_approve_enabled"] is False  # the server default
    assert first["defaults"]["auto_approve_min_confidence"] == 0.9
    changed = client.put(
        "/api/v1/settings",
        headers=auth_headers,
        json={
            "auto_approve_enabled": True,
            "auto_approve_min_confidence": 0.95,
            "auto_approve_min_hsn_confidence": 0.85,
        },
    ).json()
    assert changed["auto_approve_enabled"] is True
    assert changed["auto_approve_min_confidence"] == 0.95


def test_settings_reject_meaningless_thresholds(client, auth_headers):
    response = client.put(
        "/api/v1/settings",
        headers=auth_headers,
        json={
            "auto_approve_enabled": True,
            "auto_approve_min_confidence": 0.1,
            "auto_approve_min_hsn_confidence": 0.8,
        },
    )
    assert response.status_code == 422
