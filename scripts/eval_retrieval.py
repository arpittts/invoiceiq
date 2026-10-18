"""Compare vector-only, keyword-only and hybrid HSN/SAC retrieval (blueprint section 9).

For each labelled description, run each retrieval mode (top 5) and score:
- recall@1: the right code is the first result
- recall@5: the right code is somewhere in the top 5
- MRR: mean of 1/rank of the right code (0 if not in the top 5)
at two levels: heading (first 4 digits, what usually decides the rate) and exact code.

No LLM calls: retrieval only, so this is free and deterministic.

Usage (from the repo root):
    backend/.venv/bin/python scripts/eval_retrieval.py
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.db import SessionLocal  # noqa: E402
from app.llm.embeddings import get_embedder  # noqa: E402
from app.services.classification import search  # noqa: E402

DATASET = REPO / "evals" / "datasets" / "hsn_retrieval.jsonl"
MODES = ("vector", "keyword", "hybrid")


def rank_of(results: list[str], target: str, level: str) -> int | None:
    """1-based rank of the first result matching the target, or None."""
    for rank, code in enumerate(results, start=1):
        if (code[:4] == target[:4]) if level == "heading" else (code == target):
            return rank
    return None


def main() -> None:
    items = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    db = SessionLocal()
    embedder = get_embedder()

    scores = {
        mode: {level: {"r1": 0, "r5": 0, "mrr": 0.0} for level in ("heading", "code")}
        for mode in MODES
    }
    per_item = []
    for item in items:
        row = {"id": item["id"], "description": item["description"], "label": item["code"]}
        for mode in MODES:
            found = [r.code for r in search(db, embedder, item["description"], mode=mode, limit=5)]
            row[mode] = found
            for level in ("heading", "code"):
                rank = rank_of(found, item["code"], level)
                s = scores[mode][level]
                s["r1"] += rank == 1
                s["r5"] += rank is not None
                s["mrr"] += 1 / rank if rank else 0.0
        per_item.append(row)
    n = len(items)

    lines = [
        f"HSN/SAC retrieval on {n} labelled descriptions (top 5 per mode)",
        "",
        "| Mode | Heading recall@1 | Heading recall@5 | Heading MRR "
        "| Code recall@1 | Code recall@5 | Code MRR |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    summary = {}
    for mode in MODES:
        h, c = scores[mode]["heading"], scores[mode]["code"]
        summary[mode] = {
            f"{lvl}_{k}": round(v / n, 4)
            for lvl in ("heading", "code")
            for k, v in scores[mode][lvl].items()
        }
        lines.append(
            f"| {mode} | {h['r1'] / n:.1%} | {h['r5'] / n:.1%} | {h['mrr'] / n:.3f} "
            f"| {c['r1'] / n:.1%} | {c['r5'] / n:.1%} | {c['mrr'] / n:.3f} |"
        )
    table = "\n".join(lines)
    print(table)

    reports = REPO / "evals" / "reports"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (reports / f"hsn-retrieval-{stamp}.json").write_text(
        json.dumps({"items": n, "summary": summary, "per_item": per_item}, indent=2) + "\n"
    )
    (reports / f"hsn-retrieval-{stamp}.md").write_text(table + "\n")
    print(f"\nReports: evals/reports/hsn-retrieval-{stamp}.json and .md")


if __name__ == "__main__":
    main()
