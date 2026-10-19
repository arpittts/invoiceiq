"""Review flow: list, page images, corrections, dismissing issues, approve, reject."""

from sqlalchemy import select

from app.models import ReviewEvent
from tests.conftest import register_and_login
from tests.test_extraction_schema import printed
from tests.test_extraction_service import upload, valid_reply

API = "/api/v1/invoices"


def detail(client, headers, invoice_id):
    return client.get(f"{API}/{invoice_id}", headers=headers).json()


def rule_codes(body, open_only=True):
    return {i["rule_code"] for i in body["validation_issues"] if not (open_only and i["resolved"])}


def new_invoice(client, headers, provider, **overrides) -> int:
    provider.replies.append(valid_reply(**overrides))
    return upload(client, headers)


# --- list and page images ----------------------------------------------------


def test_list_shows_own_invoices_with_filters_and_counts(client, auth_headers, extraction_on):
    clean = new_invoice(client, auth_headers, extraction_on)
    wrong_total = new_invoice(
        client,
        auth_headers,
        extraction_on,
        invoice_number=printed("X-2"),
        total=printed("4,000.00"),
    )
    other = register_and_login(client, "other@example.com")
    new_invoice(client, other, extraction_on)

    body = client.get(API, headers=auth_headers).json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [wrong_total, clean]  # newest first
    counts = {item["id"]: item["open_errors"] for item in body["items"]}
    assert counts == {clean: 0, wrong_total: 1}

    assert client.get(API, params={"vendor": "sharma"}, headers=auth_headers).json()["total"] == 2
    assert client.get(API, params={"vendor": "nobody"}, headers=auth_headers).json()["total"] == 0
    assert client.get(API, params={"status": "approved"}, headers=auth_headers).json()["total"] == 0
    page = client.get(API, params={"limit": 1, "offset": 1}, headers=auth_headers).json()
    assert [item["id"] for item in page["items"]] == [clean] and page["total"] == 2


def test_page_image_is_served_to_the_owner_only(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    response = client.get(f"{API}/{invoice_id}/pages/1", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    assert client.get(f"{API}/{invoice_id}/pages/2", headers=auth_headers).status_code == 404
    other = register_and_login(client, "other@example.com")
    assert client.get(f"{API}/{invoice_id}/pages/1", headers=other).status_code == 404


def test_detail_includes_field_confidence(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on, total=printed("3,840.00", 0.6))
    scores = detail(client, auth_headers, invoice_id)["field_confidence"]
    assert scores["total"] == 0.6
    assert scores["line_items[1].quantity"] == 0.95


# --- corrections -------------------------------------------------------------


def test_correcting_a_field_logs_it_and_clears_the_issue(client, auth_headers, extraction_on, db):
    invoice_id = new_invoice(client, auth_headers, extraction_on, total=printed("4,000.00"))
    assert rule_codes(detail(client, auth_headers, invoice_id)) == {"TOTAL_MATH"}

    response = client.patch(f"{API}/{invoice_id}", headers=auth_headers, json={"total": "3840.00"})
    assert response.status_code == 200
    assert rule_codes(response.json()) == set()  # validation re-ran

    [event] = db.scalars(select(ReviewEvent)).all()
    assert (event.field, event.old_value, event.new_value) == ("total", "4000", "3840")


def test_unchanged_values_are_not_logged(client, auth_headers, extraction_on, db):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    client.patch(f"{API}/{invoice_id}", headers=auth_headers, json={"total": "3840"})
    assert db.scalars(select(ReviewEvent)).all() == []  # 3840 == 3840.00


def test_line_item_edits_are_logged_per_field(client, auth_headers, extraction_on, db):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    line = detail(client, auth_headers, invoice_id)["line_items"][0]
    line.pop("position"), line.pop("confidence")
    edited = {**line, "quantity": "3"}

    body = client.patch(
        f"{API}/{invoice_id}", headers=auth_headers, json={"line_items": [edited]}
    ).json()
    assert "LINE_MATH" in rule_codes(body)  # 3 × 1500 no longer matches 3000
    assert body["line_items"][0]["confidence"] is None  # edited by a human now
    fields = [e.field for e in db.scalars(select(ReviewEvent)).all()]
    assert fields == ["line_items[1].quantity"]

    client.patch(f"{API}/{invoice_id}", headers=auth_headers, json={"line_items": []})
    assert db.scalars(select(ReviewEvent.field).order_by(ReviewEvent.id)).all()[-1] == (
        "line_items[1]"
    )


def test_gstin_corrections_are_normalised(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    body = client.patch(
        f"{API}/{invoice_id}", headers=auth_headers, json={"buyer_gstin": " 27aapfu0939f1zv "}
    ).json()
    assert body["buyer_gstin"] == "27AAPFU0939F1ZV"


# --- dismissing issues and approving -----------------------------------------


def test_approve_is_blocked_until_errors_are_fixed_or_dismissed(
    client, auth_headers, extraction_on
):
    invoice_id = new_invoice(client, auth_headers, extraction_on, total=printed("4,000.00"))
    blocked = client.post(f"{API}/{invoice_id}/approve", headers=auth_headers)
    assert blocked.status_code == 409
    assert "TOTAL_MATH" in blocked.json()["detail"]

    [issue] = detail(client, auth_headers, invoice_id)["validation_issues"]
    dismissed = client.post(
        f"{API}/{invoice_id}/issues/{issue['id']}/dismiss", headers=auth_headers
    )
    assert dismissed.json()["validation_issues"][0]["resolved"] is True

    approved = client.post(f"{API}/{invoice_id}/approve", headers=auth_headers).json()
    assert approved["status"] == "approved" and approved["approved_at"]


def test_dismissed_issue_stays_dismissed_after_another_edit(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on, total=printed("4,000.00"))
    [issue] = detail(client, auth_headers, invoice_id)["validation_issues"]
    client.post(f"{API}/{invoice_id}/issues/{issue['id']}/dismiss", headers=auth_headers)

    body = client.patch(
        f"{API}/{invoice_id}", headers=auth_headers, json={"buyer_name": "Mehta Motors Ltd"}
    ).json()
    assert [(i["rule_code"], i["resolved"]) for i in body["validation_issues"]] == [
        ("TOTAL_MATH", True)
    ]


def test_approved_and_rejected_invoices_are_final(client, auth_headers, extraction_on):
    approved = new_invoice(client, auth_headers, extraction_on)
    client.post(f"{API}/{approved}/approve", headers=auth_headers)
    assert (
        client.patch(f"{API}/{approved}", headers=auth_headers, json={"total": "1"}).status_code
        == 409
    )
    assert client.post(f"{API}/{approved}/reject", headers=auth_headers).status_code == 409

    rejected = new_invoice(client, auth_headers, extraction_on, invoice_number=printed("R-1"))
    assert client.post(f"{API}/{rejected}/reject", headers=auth_headers).json()["status"] == (
        "rejected"
    )
    assert client.post(f"{API}/{rejected}/approve", headers=auth_headers).status_code == 409


def test_an_approved_invoice_can_be_moved_back_to_review(client, auth_headers, db, extraction_on):
    from app.services.ledger_qa import run_readonly

    invoice_id = new_invoice(client, auth_headers, extraction_on)
    client.post(f"{API}/{invoice_id}/approve", headers=auth_headers)
    user_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["id"]
    assert run_readonly("SELECT COUNT(*) FROM v_invoices", user_id)[1] == [[1]]

    body = client.post(f"{API}/{invoice_id}/reopen", headers=auth_headers).json()
    assert body["status"] == "needs_review" and body["approved_at"] is None
    assert run_readonly("SELECT COUNT(*) FROM v_invoices", user_id)[1] == [[0]]  # out of ledger
    # Editable again, and both decisions are in the audit trail.
    assert (
        client.patch(
            f"{API}/{invoice_id}", headers=auth_headers, json={"buyer_name": "Fixed Ltd"}
        ).status_code
        == 200
    )
    events = db.scalars(
        select(ReviewEvent).where(ReviewEvent.field == "status").order_by(ReviewEvent.id)
    ).all()
    assert [(e.old_value, e.new_value) for e in events] == [
        ("needs_review", "approved"),
        ("approved", "needs_review"),
    ]


def test_a_rejected_invoice_can_be_moved_back_to_review(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    client.post(f"{API}/{invoice_id}/reject", headers=auth_headers)
    body = client.post(f"{API}/{invoice_id}/reopen", headers=auth_headers).json()
    assert body["status"] == "needs_review"
    assert client.post(f"{API}/{invoice_id}/approve", headers=auth_headers).status_code == 200


def test_reopening_rechecks_for_duplicates(client, auth_headers, extraction_on):
    """While A was rejected, its twin B was approved: reopening A flags the duplicate."""
    first = new_invoice(client, auth_headers, extraction_on)
    client.post(f"{API}/{first}/reject", headers=auth_headers)
    second = new_invoice(client, auth_headers, extraction_on)  # same GSTIN and number
    client.post(f"{API}/{second}/approve", headers=auth_headers)
    body = client.post(f"{API}/{first}/reopen", headers=auth_headers).json()
    assert "DUPLICATE" in rule_codes(body)
    assert client.post(f"{API}/{first}/approve", headers=auth_headers).status_code == 409


def test_only_finished_invoices_can_be_reopened(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on)  # needs_review
    response = client.post(f"{API}/{invoice_id}/reopen", headers=auth_headers)
    assert response.status_code == 409 and "already open" in response.json()["detail"]


def test_someone_elses_invoice_cannot_be_reopened(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    client.post(f"{API}/{invoice_id}/approve", headers=auth_headers)
    stranger = register_and_login(client, "stranger@example.com")
    assert client.post(f"{API}/{invoice_id}/reopen", headers=stranger).status_code == 404


def test_the_ledger_never_gets_the_same_invoice_twice(client, auth_headers, extraction_on):
    first = new_invoice(client, auth_headers, extraction_on)
    second = new_invoice(client, auth_headers, extraction_on)  # same GSTIN and number
    assert client.post(f"{API}/{first}/approve", headers=auth_headers).status_code == 200

    for issue in detail(client, auth_headers, second)["validation_issues"]:
        client.post(f"{API}/{second}/issues/{issue['id']}/dismiss", headers=auth_headers)
    response = client.post(f"{API}/{second}/approve", headers=auth_headers)
    assert response.status_code == 409
    assert "already has this vendor GSTIN" in response.json()["detail"]
    assert detail(client, auth_headers, second)["status"] == "needs_review"


def test_failed_invoice_can_be_entered_by_hand(client, auth_headers, extraction_on, db):
    extraction_on.replies += ["x", "y", "z"]  # extraction fails
    invoice_id = upload(client, auth_headers)
    assert detail(client, auth_headers, invoice_id)["status"] == "failed"

    body = client.patch(
        f"{API}/{invoice_id}",
        headers=auth_headers,
        json={
            "vendor_gstin": "27AAPFU0939F1ZV",
            "invoice_number": "M-1",
            "invoice_date": "2026-10-01",
            "total": "100",
        },
    ).json()
    assert body["status"] == "needs_review"
    assert "status" in db.scalars(select(ReviewEvent.field)).all()


def test_review_actions_on_other_users_invoices_are_404(client, auth_headers, extraction_on):
    invoice_id = new_invoice(client, auth_headers, extraction_on)
    other = register_and_login(client, "other@example.com")
    assert client.patch(f"{API}/{invoice_id}", headers=other, json={}).status_code == 404
    assert client.post(f"{API}/{invoice_id}/approve", headers=other).status_code == 404
