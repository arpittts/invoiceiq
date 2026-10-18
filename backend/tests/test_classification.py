"""HSN/SAC lookup: retrieval, rank fusion, LLM selection, pipeline and API."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.llm.embeddings import HashingEmbedder
from app.llm.provider import FakeProvider
from app.models import HsnCode
from app.services.classification import (
    keyword_ranking,
    printed_code_ranking,
    reciprocal_rank_fusion,
    search,
    select_codes,
    vector_ranking,
)
from tests.test_extraction_service import upload, valid_reply

API = "/api/v1/invoices"

# A tiny slice of the official list (descriptions shortened), enough to test ranking.
CORPUS = [
    ("1006", "HSN", "Rice", None, "10", "5"),
    ("10063020", "HSN", "Basmati rice, semi-milled or wholly milled", "Rice", "10", "5"),
    ("2523", "HSN", "Portland cement, aluminous cement, slag cement", None, "25", "18"),
    ("4011", "HSN", "New pneumatic tyres, of rubber", None, "40", "18"),
    ("40114000", "HSN", "Tyres of a kind used on motorcycles", "New pneumatic tyres", "40", "18"),
    ("8539", "HSN", "Electric filament or discharge lamps; LED lamps", None, "85", "18"),
    ("85395000", "HSN", "Light-emitting diode (LED) lamps", "Electric lamps", "85", "18"),
    ("8708", "HSN", "Parts and accessories of motor vehicles", None, "87", "18"),
    (
        "87083000",
        "HSN",
        "Brakes and servo-brakes and parts thereof",
        "Parts of motor vehicles",
        "87",
        "18",
    ),
    ("8714", "HSN", "Parts and accessories of motorcycles", None, "87", "18"),
    ("998719", "SAC", "Maintenance and repair services of other goods", None, "99", "18"),
]


@pytest.fixture
def corpus(db) -> dict[str, int]:
    """Seed the corpus with stand-in embeddings. Returns code -> id."""
    embedder = HashingEmbedder()
    rows = []
    for code, kind, description, heading, chapter, rate in CORPUS:
        text = f"{code} {description} {heading or ''}"
        rows.append(
            HsnCode(
                code=code,
                kind=kind,
                description=description,
                heading=heading,
                chapter=chapter,
                default_gst_rate=Decimal(rate),
                source_note="test corpus",
                embedding=embedder.embed_documents([text])[0],
            )
        )
    db.add_all(rows)
    db.commit()
    return {row.code: row.id for row in rows}


def codes_of(db, ids):
    by_id = {row.id: row.code for row in db.scalars(select(HsnCode))}
    return [by_id[i] for i in ids]


# --- retrieval and fusion -------------------------------------------------------


def test_rank_fusion_rewards_items_found_by_several_retrievers():
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 4], [3]])
    assert [item for item, _ in fused][:2] == [3, 1]  # 3 is in all three lists
    assert fused[0][1] == pytest.approx(1 / 63 + 1 / 61 + 1 / 61)


def test_keyword_search_finds_exact_terms(db, corpus):
    assert codes_of(db, keyword_ranking(db, "cement 50kg bag"))[0] == "2523"


def test_vector_search_ranks_by_similarity(db, corpus):
    query = HashingEmbedder().embed_query("LED lamps 9W")
    assert codes_of(db, vector_ranking(db, query))[0] in {"85395000", "8539"}


def test_printed_code_finds_its_family_general_first(db, corpus):
    assert codes_of(db, printed_code_ranking(db, "8708 30"))[:2] == ["87083000"]
    assert codes_of(db, printed_code_ranking(db, "HSN 8539"))[:2] == ["8539", "85395000"]
    assert printed_code_ranking(db, "12") == []  # too short to mean anything


def test_hybrid_search_combines_retrievers(db, corpus):
    found = [row.code for row in search(db, HashingEmbedder(), "Basmati Rice 25kg bag")]
    assert found[0] in {"10063020", "1006"}
    assert len(found) <= 5


# --- selection ------------------------------------------------------------------


def choices(*items):
    return json.dumps(
        {
            "choices": [
                dict(zip(("line", "candidate_id", "confidence", "reason"), i, strict=True))
                for i in items
            ]
        }
    )


def test_selection_trusts_only_cited_confident_candidates(db, corpus):
    rows = {row.code: row for row in db.scalars(select(HsnCode))}
    candidates = {1: [rows["8714"], rows["87083000"]], 2: [rows["2523"]], 3: [rows["4011"]]}
    provider = FakeProvider(
        [
            choices(
                (1, rows["8714"].id, 0.9, "Candidate: motorcycle parts"),
                (2, 99999, 0.95, "made-up id"),  # not one of its candidates
                (3, rows["4011"].id, 0.3, "unsure"),
            )  # below the confidence bar
        ]
    )
    lines = [{"line": n, "description": "x", "printed_code": None} for n in (1, 2, 3)]
    result = select_codes(provider, "fake-model", lines, candidates)

    assert result[1].code.code == "8714"
    assert result[2].code is None and "unknown candidate" in result[2].reason
    assert result[3].code is None and result[3].confidence == 0.3
    assert provider.calls[0]["images"] == []  # text-only call
    assert "untrusted" in provider.calls[0]["system"]


# --- the pipeline -----------------------------------------------------------------


def test_upload_suggests_a_cited_code_and_flags_rate_mismatch(
    client, auth_headers, extraction_on, corpus
):
    # The invoice prints HSN 8708 30 at 28% for a brake pad set; the lookup picks
    # 87083000 (brakes and parts), whose default rate is 18%.
    extraction_on.replies = [
        valid_reply(),
        choices((1, corpus["87083000"], 0.85, f"Candidate {corpus['87083000']}: brakes")),
    ]
    invoice_id = upload(client, auth_headers)
    body = client.get(f"{API}/{invoice_id}", headers=auth_headers).json()

    line = body["line_items"][0]
    assert line["suggested_hsn_code"] == "87083000"
    assert Decimal(line["suggested_gst_rate"]) == 18
    assert line["hsn_confidence"] == 0.85
    assert line["hsn_source"]["description"].startswith("Brakes and servo-brakes")
    codes = {i["rule_code"] for i in body["validation_issues"]}
    assert "RATE_MISMATCH" in codes  # 28% printed vs 18% default
    assert "HSN_MISMATCH" not in codes  # same heading 8708
    assert body["status"] == "needs_review"


def test_a_code_outside_the_retrieved_candidates_is_refused(
    client, auth_headers, extraction_on, corpus, db
):
    retrieved = {
        row.code for row in search(db, HashingEmbedder(), "Brake pad set", printed_code="8708 30")
    }
    outside = next(code for code in corpus if code not in retrieved)
    extraction_on.replies = [valid_reply(), choices((1, corpus[outside], 0.99, "not offered"))]
    invoice_id = upload(client, auth_headers)
    line = client.get(f"{API}/{invoice_id}", headers=auth_headers).json()["line_items"][0]
    assert line["suggested_hsn_code"] is None
    assert line["hsn_reason"] == "Model cited an unknown candidate"


def test_failed_selection_never_blocks_the_invoice(client, auth_headers, extraction_on, corpus):
    extraction_on.replies = [valid_reply(), "not json"]
    invoice_id = upload(client, auth_headers)
    body = client.get(f"{API}/{invoice_id}", headers=auth_headers).json()
    assert body["status"] == "needs_review"
    assert body["line_items"][0]["suggested_hsn_code"] is None


def test_no_corpus_means_no_lookup_call(client, auth_headers, extraction_on):
    extraction_on.replies = [valid_reply()]
    upload(client, auth_headers)
    assert len(extraction_on.calls) == 1  # extraction only


def test_corrections_keep_or_refresh_suggestions(client, auth_headers, extraction_on, corpus):
    extraction_on.replies = [
        valid_reply(),
        choices((1, corpus["87083000"], 0.85, "brakes")),
    ]
    invoice_id = upload(client, auth_headers)
    line = client.get(f"{API}/{invoice_id}", headers=auth_headers).json()["line_items"][0]
    keep = {
        k: line[k]
        for k in (
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
        )
    }

    # A different field changed: the suggestion is kept, no new LLM call.
    body = client.patch(
        f"{API}/{invoice_id}", headers=auth_headers, json={"line_items": [{**keep, "unit": "Set"}]}
    ).json()
    assert body["line_items"][0]["suggested_hsn_code"] == "87083000"
    assert len(extraction_on.calls) == 2

    # The description changed: the line is classified again.
    extraction_on.replies.append(choices((1, corpus["2523"], 0.9, "cement")))
    body = client.patch(
        f"{API}/{invoice_id}",
        headers=auth_headers,
        json={"line_items": [{**keep, "description": "Cement 50kg bag"}]},
    ).json()
    assert body["line_items"][0]["suggested_hsn_code"] == "2523"
    assert len(extraction_on.calls) == 3


# --- API --------------------------------------------------------------------------


def test_hsn_search_endpoint(client, auth_headers, corpus):
    response = client.get(
        "/api/v1/hsn/search", params={"q": "portland cement"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()[0]["code"] == "2523"
    keyword_only = client.get(
        "/api/v1/hsn/search",
        headers=auth_headers,
        params={"q": "tyres", "mode": "keyword", "limit": 2},
    ).json()
    assert len(keyword_only) <= 2 and all("yre" in r["description"] for r in keyword_only)
    assert client.get("/api/v1/hsn/search", params={"q": "cement"}).status_code == 401
