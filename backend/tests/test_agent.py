"""Graph-level tests for the invoice agent, driven by a fake LLM (no network, no cost).

Each test feeds scripted model replies and checks the route the graph took
(state["path"]), what it saved, and that every loop is bounded.
"""

import json

import pymupdf
import pytest
from langgraph.errors import GraphRecursionError
from sqlalchemy import select

from app.agent.graph import build_graph, run_agent
from app.agent.state import AgentDeps
from app.config import get_settings
from app.llm.embeddings import HashingEmbedder
from app.llm.provider import FakeProvider
from app.models import ExtractionRun, Invoice, ReviewEvent, ValidationIssue
from app.services.pdf import PageImage
from tests.test_classification import seed_corpus
from tests.test_extraction_schema import make_output, printed
from tests.test_extraction_service import upload, valid_reply

# A real (blank) 10x10 PNG: the second read re-renders the page's pixels.
PAGE = PageImage(
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False).tobytes("png"),
    "image/png",
    10,
    10,
)
BAD_TOTAL = {"total": printed("4,000.00")}  # line totals add up to 3,840.00


def new_invoice(client, headers) -> int:
    """An uploaded invoice still in 'processing' (conftest keeps extraction off)."""
    return upload(client, headers)


def run(db, invoice_id, replies, provider=None):
    provider = provider or FakeProvider(list(replies))
    state = run_agent(AgentDeps(db, provider, HashingEmbedder()), invoice_id, [PAGE])
    db.commit()
    return state, provider


def two_line_reply(**line2_overrides) -> str:
    """A consistent two-line invoice: two identical brake-pad lines."""
    data = json.loads(valid_reply())
    second = {**data["line_items"][0], **line2_overrides}
    data["line_items"] = [data["line_items"][0], second]
    for name, value in (
        ("subtotal", "6,000.00"),
        ("cgst", "840.00"),
        ("sgst", "840.00"),
        ("total", "7,680.00"),
    ):
        data[name] = printed(value)
    return json.dumps(data)


# --- shape --------------------------------------------------------------------


def test_graph_has_the_designed_shape():
    graph = build_graph(AgentDeps(None, None, None)).get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert {
        ("__start__", "extract"),
        ("extract", "validate"),
        ("extract", "finalize"),
        ("validate", "re_extract"),
        ("re_extract", "validate"),
        ("validate", "save"),
        ("save", "classify"),
        ("classify", "final_check"),
        ("final_check", "decide"),
        ("decide", "verify"),
        ("decide", "finalize"),
        ("verify", "finalize"),
        ("finalize", "__end__"),
    } == edges


# --- routes ---------------------------------------------------------------------


def test_clean_invoice_goes_straight_to_review(client, auth_headers, db):
    invoice_id = new_invoice(client, auth_headers)
    state, provider = run(db, invoice_id, [valid_reply()])

    assert state["path"] == [
        "extract",
        "validate",
        "save",
        "classify",
        "final_check",
        "decide",
        "finalize",
    ]
    assert state["decision"] == "review"  # auto-approve is off by default
    assert db.get(Invoice, invoice_id).status == "needs_review"
    assert len(provider.calls) == 1


def test_math_error_triggers_a_targeted_re_read_that_merges_only_the_failing_field(
    client, auth_headers, db
):
    invoice_id = new_invoice(client, auth_headers)
    first = valid_reply(**BAD_TOTAL)
    # The second read fixes the total but also reads the vendor name differently.
    second = valid_reply(vendor_name=printed("Something Else"))
    state, provider = run(db, invoice_id, [first, second])

    assert state["path"][:4] == ["extract", "validate", "re_extract", "validate"]
    assert state["re_extractions"] == 1
    focus = provider.calls[1]["instruction"]
    assert "failed these checks" in focus and "- total:" in focus

    invoice = db.get(Invoice, invoice_id)
    assert str(invoice.total) == "3840.00"  # fixed by the re-read
    assert invoice.vendor_name == "Sharma Auto Parts"  # kept from the first read
    assert [run.attempt for run in db.scalars(select(ExtractionRun))] == [1, 2]
    assert not [i for i in invoice.validation_issues if i.rule_code == "TOTAL_MATH"]


def test_a_persistent_error_stops_after_two_re_reads_and_goes_to_review(client, auth_headers, db):
    invoice_id = new_invoice(client, auth_headers)
    state, provider = run(db, invoice_id, [valid_reply(**BAD_TOTAL)] * 3)

    assert state["path"].count("re_extract") == 2
    assert state["path"].count("validate") == 3
    assert len(provider.calls) == 3
    invoice = db.get(Invoice, invoice_id)
    assert invoice.status == "needs_review"
    assert any(i.rule_code == "TOTAL_MATH" for i in invoice.validation_issues)


def test_out_of_budget_means_no_re_read(client, auth_headers, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_tokens_per_invoice", 1000)  # one call uses 1500
    invoice_id = new_invoice(client, auth_headers)
    state, provider = run(db, invoice_id, [valid_reply(**BAD_TOTAL)] * 3)
    assert "re_extract" not in state["path"]
    assert len(provider.calls) == 1


def test_a_line_error_replaces_only_that_line(client, auth_headers, db):
    invoice_id = new_invoice(client, auth_headers)
    first = two_line_reply(quantity=printed("3"))  # line 2: 3 x 1500 != 3000
    second = json.loads(two_line_reply())  # line 2 read correctly
    second["line_items"][0]["description"] = printed("Re-read line one")  # line 1 differs
    state, _ = run(db, invoice_id, [first, json.dumps(second)])

    lines = db.get(Invoice, invoice_id).line_items
    assert lines[0].description == "Brake pad set"  # line 1 kept from the first read
    assert lines[1].quantity == 2  # line 2 taken from the re-read
    assert state["re_extractions"] == 1


def test_a_skipped_line_brings_in_the_re_read_line_list(client, auth_headers, db):
    invoice_id = new_invoice(client, auth_headers)
    one_line_but_two_line_totals = json.loads(two_line_reply())
    one_line_but_two_line_totals["line_items"] = one_line_but_two_line_totals["line_items"][:1]
    run(db, invoice_id, [json.dumps(one_line_but_two_line_totals), two_line_reply()])
    assert len(db.get(Invoice, invoice_id).line_items) == 2


def test_unreadable_invoice_goes_straight_to_failed(client, auth_headers, db):
    invoice_id = new_invoice(client, auth_headers)
    state, _ = run(db, invoice_id, ["x", "y", "z"])
    assert state["path"] == ["extract", "finalize"]
    assert db.get(Invoice, invoice_id).status == "failed"
    assert len(db.scalars(select(ExtractionRun)).all()) == 3  # every attempt is kept


# --- auto-approve -----------------------------------------------------------------


def choose(code_id):
    return json.dumps(
        {
            "choices": [
                {
                    "line": 1,
                    "candidate_id": code_id,
                    "confidence": 0.9,
                    "reason": f"Candidate {code_id}",
                }
            ]
        }
    )


@pytest.fixture
def auto_approve(monkeypatch):
    monkeypatch.setattr(get_settings(), "auto_approve_enabled", True)


def spotless_reply() -> str:
    """Every field at full confidence, rate matching the suggested code's 18%."""
    data = json.loads(valid_reply())
    for value in data.values():
        if isinstance(value, dict):
            value["confidence"] = 1.0
    line = data["line_items"][0]
    for value in line.values():
        value["confidence"] = 1.0
    line["gst_rate"]["value"] = "18%"
    line["cgst_amount"]["value"] = line["sgst_amount"]["value"] = "270.00"
    line["line_total"]["value"] = "3,540.00"
    for name, value in (("cgst", "270.00"), ("sgst", "270.00"), ("total", "3,540.00")):
        data[name]["value"] = value
    return json.dumps(data)


def test_spotless_confident_invoice_is_auto_approved(client, auth_headers, db, auto_approve):
    codes = seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    # First read, HSN choice, then the second read, which agrees.
    state, provider = run(
        db, invoice_id, [spotless_reply(), choose(codes["87083000"]), spotless_reply()]
    )

    assert state["decision"] == "auto_approve", state["reason"]
    assert "second read agreed" in state["reason"]
    assert state["path"][-2:] == ["verify", "finalize"]
    second = provider.calls[2]
    assert second["images"][0].media_type == "image/jpeg"  # rendered differently
    assert "independent second reading" in second["instruction"]
    invoice = db.get(Invoice, invoice_id)
    assert invoice.status == "approved" and invoice.approved_at is not None
    event = db.scalars(select(ReviewEvent)).one()
    assert event.new_value == "approved_by_agent"


def test_reads_that_disagree_go_to_a_person(client, auth_headers, db, auto_approve):
    """The case no rule catches: one read fills in a line total, the other leaves it out."""
    codes = seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    second = json.loads(spotless_reply())
    second["line_items"][0]["line_total"]["value"] = None
    state, _ = run(
        db, invoice_id, [spotless_reply(), choose(codes["87083000"]), json.dumps(second)]
    )

    assert state["decision"] == "review"
    assert state["reason"] == "Two reads disagree on line_items[1].line_total"
    invoice = db.get(Invoice, invoice_id)
    assert invoice.status == "needs_review"
    [issue] = db.scalars(
        select(ValidationIssue).where(
            ValidationIssue.invoice_id == invoice_id, ValidationIssue.rule_code == "READS_DISAGREE"
        )
    ).all()
    assert issue.field == "line_items[1].line_total" and issue.severity == "warning"


def test_a_failed_second_read_confirms_nothing(client, auth_headers, db, auto_approve):
    codes = seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    replies = [spotless_reply(), choose(codes["87083000"])] + ["not json"] * 3
    state, _ = run(db, invoice_id, replies)
    assert state["decision"] == "review" and "second read failed" in state["reason"]


def test_the_agreement_check_can_be_switched_off(
    client, auth_headers, db, auto_approve, monkeypatch
):
    monkeypatch.setattr(get_settings(), "auto_approve_require_agreement", False)
    codes = seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    state, provider = run(db, invoice_id, [spotless_reply(), choose(codes["87083000"])])
    assert state["decision"] == "auto_approve" and "verify" not in state["path"]
    assert len(provider.calls) == 2


def test_review_decisions_never_pay_for_a_second_read(client, auth_headers, db):
    invoice_id = new_invoice(client, auth_headers)  # auto-approve is off by default
    state, provider = run(db, invoice_id, [valid_reply()])
    assert "verify" not in state["path"] and len(provider.calls) == 1


def test_a_user_who_switched_auto_approve_off_is_respected(client, auth_headers, db, auto_approve):
    """The server default is on here, but this user's own setting wins."""
    codes = seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    client.put(
        "/api/v1/settings",
        headers=auth_headers,
        json={
            "auto_approve_enabled": False,
            "auto_approve_min_confidence": 0.9,
            "auto_approve_min_hsn_confidence": 0.8,
        },
    )
    db.expire_all()
    state, _ = run(db, invoice_id, [spotless_reply(), choose(codes["87083000"])])
    assert state["decision"] == "review" and "off" in state["reason"]


def test_a_warning_blocks_auto_approve(client, auth_headers, db, auto_approve):
    codes = seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    # valid_reply prints 28%, the suggested code's default is 18%: RATE_MISMATCH warning.
    state, _ = run(db, invoice_id, [make_output().model_dump_json(), choose(codes["87083000"])])
    assert state["decision"] == "review"
    assert "issue" in state["reason"]


def test_low_confidence_blocks_auto_approve(client, auth_headers, db, auto_approve):
    seed_corpus(db)
    invoice_id = new_invoice(client, auth_headers)
    data = json.loads(spotless_reply())
    data["total"]["confidence"] = 0.7
    state, _ = run(db, invoice_id, [json.dumps(data), choose(None)])
    assert state["decision"] == "review"
    assert "Confidence" in state["reason"]


def test_missing_hsn_code_blocks_auto_approve(client, auth_headers, db, auto_approve):
    invoice_id = new_invoice(client, auth_headers)  # no corpus: no codes can be suggested
    state, _ = run(db, invoice_id, [spotless_reply()])
    assert state["decision"] == "review"
    assert "HSN/SAC" in state["reason"]


# --- no infinite loops ------------------------------------------------------------


def test_the_recursion_limit_stops_a_runaway_loop(client, auth_headers, db, monkeypatch):
    """Even with the re-read cap set absurdly high, LangGraph refuses to loop forever."""
    monkeypatch.setattr(get_settings(), "max_reextractions", 1000)
    monkeypatch.setattr(get_settings(), "max_tokens_per_invoice", 10**9)
    invoice_id = new_invoice(client, auth_headers)
    with pytest.raises(GraphRecursionError):
        run(db, invoice_id, [valid_reply(**BAD_TOTAL)] * 100)
