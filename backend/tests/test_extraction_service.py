import json
from decimal import Decimal

import pymupdf
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.llm.provider import FakeProvider
from app.models import ExtractionRun
from app.services import extraction
from app.services.extraction import extract_from_pages
from app.services.pdf import PageImage
from tests.conftest import register_and_login
from tests.test_extraction_schema import make_output, printed

PAGE = PageImage(b"png-bytes", "image/png", 100, 100)


def valid_reply(**overrides) -> str:
    return make_output(**overrides).model_dump_json()


def bad_confidence_reply() -> str:
    data = json.loads(valid_reply())
    data["total"]["confidence"] = 1.7
    return json.dumps(data)


def run(provider, max_retries=2):
    return extract_from_pages(
        [PAGE], provider, model="claude-opus-5-5", effort="medium",
        max_retries=max_retries, trace_metadata={},
    )  # fmt: skip


# --- extract_from_pages (no database) -------------------------------------


def test_valid_first_reply_succeeds_with_cost():
    provider = FakeProvider([valid_reply()], model="claude-opus-5-5")
    outcome = run(provider)

    assert outcome.extraction is not None
    assert outcome.extraction.total.value == Decimal("3840.00")
    [attempt] = outcome.attempts
    assert attempt.issues == []
    # 1000 input tokens at $4/M + 500 output tokens at $20/M
    assert attempt.cost_usd == Decimal("0.014000")


def test_invalid_replies_are_retried_with_the_validator_message():
    provider = FakeProvider(["not json", bad_confidence_reply(), valid_reply()])
    outcome = run(provider)

    assert outcome.extraction is not None
    assert [a.issues[0]["code"] for a in outcome.attempts[:2]] == ["INVALID_JSON", "SCHEMA"]
    assert len(provider.calls) == 3
    assert "rejected by our validator" not in provider.calls[0]["instruction"]
    assert "confidence" in provider.calls[2]["instruction"]  # the SCHEMA error was passed on


def test_gives_up_after_max_retries():
    provider = FakeProvider(["nope"] * 5)
    outcome = run(provider, max_retries=2)
    assert outcome.extraction is None
    assert len(outcome.attempts) == 3  # first try + 2 retries
    assert "Invalid JSON" in outcome.error


def test_refusal_is_not_retried():
    provider = FakeProvider([None, valid_reply()])
    outcome = run(provider)
    assert outcome.extraction is None
    assert [a.issues[0]["code"] for a in outcome.attempts] == ["REFUSAL"]


def test_api_error_is_recorded_not_raised():
    class BrokenProvider:
        def generate_json(self, **kwargs):
            raise ConnectionError("network down")

    outcome = run(BrokenProvider())
    assert outcome.extraction is None
    assert outcome.attempts[0].issues[0]["code"] == "API_ERROR"


def test_extraction_call_keeps_instructions_and_document_separate():
    provider = FakeProvider([valid_reply()])
    run(provider)
    call = provider.calls[0]
    assert "untrusted document" in call["system"]  # our rules live in the system prompt
    assert call["images"] == [PAGE]  # the invoice only arrives as images
    assert "tools" not in call  # the extraction step is never given tools (section 19)


# --- full flow: upload -> background extraction -> GET ---------------------


def make_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "TAX INVOICE")
    return doc.tobytes()


@pytest.fixture
def extraction_on(monkeypatch):
    """Turn extraction on and route it to a FakeProvider the test controls."""
    monkeypatch.setattr(get_settings(), "extraction_enabled", True)
    provider = FakeProvider([], model="claude-opus-5-5")
    monkeypatch.setattr(extraction, "get_provider", lambda: provider)
    return provider


def upload(client, headers, content: bytes | None = None):
    content = content or make_pdf()  # each make_pdf() has a unique id, so a unique hash
    response = client.post(
        "/api/v1/invoices", headers=headers, files={"file": ("inv.pdf", content)}
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_upload_extracts_and_moves_to_needs_review(client, auth_headers, extraction_on, db):
    extraction_on.replies = [bad_confidence_reply(), valid_reply()]
    invoice_id = upload(client, auth_headers)  # TestClient runs background tasks before returning

    body = client.get(f"/api/v1/invoices/{invoice_id}", headers=auth_headers).json()
    assert body["status"] == "needs_review"
    assert body["vendor_gstin"] == "27AAPFU0939F1ZV"
    assert body["invoice_date"] == "2026-10-12"
    assert Decimal(body["total"]) == Decimal("3840.00")
    assert body["overall_confidence"] == 0.6
    [line] = body["line_items"]
    assert line["description"] == "Brake pad set" and Decimal(line["gst_rate"]) == 28
    assert body["extraction"]["attempts"] == 2
    assert Decimal(body["extraction"]["total_cost_usd"]) == Decimal("0.028")
    assert body["extraction"]["issues"] is None  # last attempt succeeded

    runs = db.scalars(select(ExtractionRun).order_by(ExtractionRun.attempt)).all()
    assert [r.attempt for r in runs] == [1, 2]
    assert runs[0].issues_json[0]["code"] == "SCHEMA"
    assert runs[1].raw_json["vendor_name"]["value"] == "  Sharma   Auto Parts "  # kept as returned


def test_failed_extraction_marks_invoice_failed(client, auth_headers, extraction_on):
    extraction_on.replies = ["x", "y", "z"]
    invoice_id = upload(client, auth_headers)
    body = client.get(f"/api/v1/invoices/{invoice_id}", headers=auth_headers).json()
    assert body["status"] == "failed"
    assert body["extraction"]["attempts"] == 3
    assert body["extraction"]["issues"][0]["code"] == "INVALID_JSON"


def test_missing_api_key_fails_clearly(client, auth_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_enabled", True)
    invoice_id = upload(client, auth_headers)
    body = client.get(f"/api/v1/invoices/{invoice_id}", headers=auth_headers).json()
    assert body["status"] == "failed"
    assert body["extraction"]["issues"][0]["code"] == "NOT_CONFIGURED"


def test_kill_switch_leaves_invoice_processing(client, auth_headers):
    # conftest sets EXTRACTION_ENABLED=false
    invoice_id = upload(client, auth_headers)
    body = client.get(f"/api/v1/invoices/{invoice_id}", headers=auth_headers).json()
    assert body["status"] == "processing"
    assert body["extraction"] is None


def test_duplicate_upload_is_saved_and_flagged_for_review(client, auth_headers, extraction_on):
    extraction_on.replies = [valid_reply(), valid_reply()]
    same_file = make_pdf()
    first = upload(client, auth_headers, same_file)
    second = upload(client, auth_headers, same_file)

    first_body = client.get(f"/api/v1/invoices/{first}", headers=auth_headers).json()
    assert not [i for i in first_body["validation_issues"] if i["rule_code"] == "DUPLICATE"]

    body = client.get(f"/api/v1/invoices/{second}", headers=auth_headers).json()
    assert body["status"] == "needs_review"  # saved, so a human can see and reject it
    assert body["invoice_number"] == "SAP/2026/0042"
    duplicates = [i for i in body["validation_issues"] if i["rule_code"] == "DUPLICATE"]
    assert {i["field"] for i in duplicates} == {"file", "invoice_number"}
    assert all(f"invoice {first}" in i["message"] for i in duplicates)


def test_other_users_cannot_read_an_invoice(client):
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    invoice_id = upload(client, alice)
    assert client.get(f"/api/v1/invoices/{invoice_id}", headers=bob).status_code == 404
    assert client.get("/api/v1/invoices/999999", headers=alice).status_code == 404


def test_missing_values_stay_null(client, auth_headers, extraction_on):
    extraction_on.replies = [valid_reply(buyer_name=printed(None, 0.9))]
    invoice_id = upload(client, auth_headers)
    body = client.get(f"/api/v1/invoices/{invoice_id}", headers=auth_headers).json()
    assert body["buyer_name"] is None
