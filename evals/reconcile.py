"""GSTR-2B reconciliation accuracy against known answers (no AI: deterministic).

The 20 dev invoices are approved in a fresh user's books; the synthetic monthly
GSTR-2B files from scripts/make_gstr2b.py are uploaded in date order (as a
business would, month by month), and every result is compared with labels.json:
each reported invoice's status, and each month's "ITC at risk" list.
"""

import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "evals/datasets/gstr2b"


def evaluate(db) -> dict:
    from app.models import User
    from app.security import hash_password
    from app.services import gstr2b
    from evals.ledger import add_truth_invoice, dev_truths

    user = User(email="reconcile-eval@example.com", password_hash=hash_password("not-used-123"))
    db.add(user)
    db.flush()
    for n, truth in enumerate(dev_truths(), start=1):
        add_truth_invoice(db, user, truth, n)
    db.commit()

    statements = []
    for path in sorted(DATA.glob("gstr2b-*.json")):
        parsed = gstr2b.parse(json.loads(path.read_text()))
        statements.append(gstr2b.save_statement(db, user.id, parsed, file_name=path.name))
        db.commit()

    labels = json.loads((DATA / "labels.json").read_text())
    predicted = {
        (s.period.isoformat(), row.supplier_gstin, row.invoice_number): row.match_status
        for s in statements
        for row in s.invoices
    }
    by_scenario: dict[str, Counter] = {}
    rows_out, correct = [], 0
    for label in labels["rows"]:
        got = predicted.get((label["period"], label["supplier_gstin"], label["invoice_number"]))
        ok = got == label["expected"]
        correct += ok
        by_scenario.setdefault(label["scenario"], Counter())[ok] += 1
        rows_out.append({**label, "got": got, "ok": ok})
        print(
            f"  {'OK ' if ok else 'XX '} {label['period'][:7]} {label['scenario']:<15} "
            f"{label['invoice_number']:<20} expected {label['expected']:<10} got {got}"
        )

    at_risk_ok = at_risk_total = 0
    for statement in statements:
        result = gstr2b.report(db, statement)
        got = sorted(inv.invoice_number for inv in result["books_only"])
        expected = sorted(labels["at_risk"].get(statement.period.isoformat(), []))
        at_risk_total += 1
        at_risk_ok += got == expected
        if got != expected:
            print(f"  XX  {statement.period:%Y-%m} at risk: expected {expected}, got {got}")
    total = len(labels["rows"])
    print(
        f"  Rows: {correct}/{total} correct; months with the right 'ITC at risk' list: "
        f"{at_risk_ok}/{at_risk_total}"
    )
    return {
        "summary": {
            "rows": total,
            "row_accuracy": correct / total,
            "months": at_risk_total,
            "at_risk_accuracy": at_risk_ok / at_risk_total,
            "by_scenario": {
                name: f"{counts[True]}/{counts[True] + counts[False]}"
                for name, counts in sorted(by_scenario.items())
            },
        },
        "rows": rows_out,
    }
