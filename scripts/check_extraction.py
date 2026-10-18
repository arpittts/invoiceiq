"""Run real extraction over the starter invoices and report the Phase 2 numbers.

Measures: valid-schema rate (the acceptance bar is 90%), first-try valid rate,
cost per invoice, p50/p95 latency, and a first look at field accuracy against
ground truth. With a paid model it shows a cost estimate and asks before
starting; on Gemini's free tier the cost is $0 (it still asks).

Usage (from the repo root, with LLM_API_KEY in .env):
    backend/.venv/bin/python scripts/check_extraction.py            # all 20
    backend/.venv/bin/python scripts/check_extraction.py --limit 3  # a cheap smoke test
"""

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.config import get_settings  # noqa: E402
from app.llm.pricing import PRICES  # noqa: E402
from app.schemas.extraction import InvoiceExtraction  # noqa: E402
from app.services.extraction import extract_from_pages, get_provider  # noqa: E402
from app.services.pdf import render_pages  # noqa: E402
from app.tracing import flush_traces  # noqa: E402

MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}
HEADER_FIELDS = [
    "vendor_name", "vendor_gstin", "buyer_name", "buyer_gstin", "invoice_number",
    "invoice_date", "place_of_supply", "subtotal", "cgst", "sgst", "igst", "total",
]  # fmt: skip
LINE_FIELDS = [
    "description", "hsn_sac_code", "quantity", "unit", "unit_price", "discount",
    "taxable_value", "gst_rate", "cgst_amount", "sgst_amount", "igst_amount", "line_total",
]  # fmt: skip
# Rough tokens per single-page invoice, used only for the up-front estimate.
EST_INPUT_TOKENS, EST_OUTPUT_TOKENS = 7000, 2500


def same(expected, actual) -> bool:
    """Compare a ground-truth value (string/None) with an extracted typed value."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(actual, Decimal):
        return Decimal(expected) == actual
    if hasattr(actual, "isoformat"):
        return expected == actual.isoformat()
    return " ".join(str(expected).split()) == " ".join(str(actual).split())


def score(truth: dict, extraction: InvoiceExtraction) -> tuple[int, int]:
    """(correct fields, total fields). Missing or extra line items count as wrong."""
    correct = total = 0
    for name in HEADER_FIELDS:
        total += 1
        correct += same(truth[name], getattr(extraction, name).value)
    true_lines, got_lines = truth["line_items"], extraction.line_items
    for index in range(max(len(true_lines), len(got_lines))):
        for name in LINE_FIELDS:
            total += 1
            if index < len(true_lines) and index < len(got_lines):
                correct += same(true_lines[index][name], getattr(got_lines[index], name).value)
    return correct, total


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 extraction check (calls the LLM API)")
    parser.add_argument("--manifest", default="evals/datasets/starter/manifest.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.llm_api_key:
        sys.exit("LLM_API_KEY is not set. Add it to .env first.")
    manifest = json.loads((REPO / args.manifest).read_text())[: args.limit]

    model = settings.llm_model_extract
    input_price, output_price = PRICES.get(model, (Decimal(0), Decimal(0)))
    per_try = (EST_INPUT_TOKENS * input_price + EST_OUTPUT_TOKENS * output_price) / 1_000_000
    print(f"Provider {settings.llm_provider}, model {model}, {len(manifest)} invoices.")
    print(f"Estimated cost: about ${per_try * len(manifest):.2f} "
          f"(at most ${per_try * len(manifest) * (settings.extraction_max_retries + 1):.2f} "
          "if every invoice needed every retry).")  # fmt: skip
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled.")

    provider = get_provider()
    rows = []
    for entry in manifest:
        path = REPO / entry["file"]
        pages = render_pages(path.read_bytes(), MIME[path.suffix], settings.render_max_edge)
        outcome = extract_from_pages(
            pages, provider, model=model, effort=settings.llm_effort_extract,
            max_retries=settings.extraction_max_retries,
            trace_metadata={"eval": "starter", "case": entry["id"]},
        )  # fmt: skip
        cost = sum((a.cost_usd or Decimal(0)) for a in outcome.attempts)
        latency = sum(a.latency_ms for a in outcome.attempts)
        row = {
            "id": entry["id"], "layout": entry["layout"], "format": entry["format"],
            "valid": outcome.extraction is not None, "attempts": len(outcome.attempts),
            "cost_usd": float(cost), "latency_ms": latency, "error": outcome.error,
        }  # fmt: skip
        if outcome.extraction is not None:
            truth = json.loads((REPO / entry["truth"]).read_text())
            row["fields_correct"], row["fields_total"] = score(truth, outcome.extraction)
        rows.append(row)
        accuracy = (
            f"{row['fields_correct']}/{row['fields_total']} fields"
            if row["valid"]
            else row["error"]
        )
        print(f"  {row['id']} {row['layout']:<10} {row['format']:<3} "
              f"{'valid' if row['valid'] else 'FAILED':<6} tries={row['attempts']} "
              f"${row['cost_usd']:.4f} {latency / 1000:.1f}s  {accuracy}")  # fmt: skip
    flush_traces()

    valid = [r for r in rows if r["valid"]]
    latencies = [r["latency_ms"] / 1000 for r in rows]
    summary = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": settings.llm_provider,
        "model": model,
        "effort": settings.llm_effort_extract,
        "invoices": len(rows),
        "valid_schema_rate": len(valid) / len(rows),
        "first_try_valid_rate": sum(r["valid"] and r["attempts"] == 1 for r in rows) / len(rows),
        "mean_cost_usd": statistics.mean(r["cost_usd"] for r in rows),
        "total_cost_usd": sum(r["cost_usd"] for r in rows),
        "p50_latency_s": percentile(latencies, 50),
        "p95_latency_s": percentile(latencies, 95),
        "field_accuracy": (
            sum(r["fields_correct"] for r in valid) / sum(r["fields_total"] for r in valid)
            if valid else None
        ),
    }  # fmt: skip

    reports = REPO / "evals" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (reports / f"starter-extraction-{stamp}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2) + "\n"
    )

    print("\nSummary")
    print(f"  Valid schema:     {summary['valid_schema_rate']:.0%} (acceptance bar: 90%)")
    print(f"  Valid first try:  {summary['first_try_valid_rate']:.0%}")
    if summary["field_accuracy"] is not None:
        print(f"  Field accuracy:   {summary['field_accuracy']:.1%} (exact match, valid invoices)")
    print(f"  Cost:             ${summary['mean_cost_usd']:.4f} per invoice, "
          f"${summary['total_cost_usd']:.2f} total")  # fmt: skip
    print(f"  Latency:          p50 {summary['p50_latency_s']:.1f}s, "
          f"p95 {summary['p95_latency_s']:.1f}s")  # fmt: skip
    print(f"  Report:           evals/reports/starter-extraction-{stamp}.json")
    sys.exit(0 if summary["valid_schema_rate"] >= 0.9 else 1)


if __name__ == "__main__":
    main()
