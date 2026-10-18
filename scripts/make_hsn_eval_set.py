"""Build the labelled HSN/SAC retrieval set (blueprint section 9: 150+ descriptions).

How: sample official codes across all chapters, ask the LLM how a small Indian
business would write each item on an invoice, then drop any description that
copies the official wording (that would make keyword search look better than it
is). The label is the code the description was written for; metrics are reported
at heading level (first 4 digits) and exact-code level.

Caveat (say this in interviews): the descriptions are LLM-written, so the set
measures retrieval on realistic phrasing, not on real customer invoices.

Usage (from the repo root; uses the configured LLM, free on Gemini):
    backend/.venv/bin/python scripts/make_hsn_eval_set.py --count 200
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import HsnCode  # noqa: E402
from app.services.extraction import get_provider  # noqa: E402

OUT = REPO / "evals" / "datasets" / "hsn_retrieval.jsonl"
STOPWORDS = {
    "and",
    "or",
    "of",
    "the",
    "for",
    "with",
    "other",
    "than",
    "not",
    "in",
    "a",
    "an",
    "to",
    "whether",
    "including",
    "nes",
    "etc",
    "kind",
    "used",
    "made",
    "its",
    "their",
    "thereof",
}

PROMPT = """You write test data for an Indian GST software product.

For each official HSN/SAC code below, write ONE line-item description exactly as a small
Indian shop or service provider would print it on an invoice. Rules:
- Short and natural (3 to 10 words), with a realistic size, pack or variant where it fits.
- Use everyday trade words, not the official tariff wording. Do not copy distinctive words
  from the official description when a common synonym exists.
- No brand names. Do not mention the code.
- The item must still clearly belong to that code, so an expert could classify it.

Return one entry per code, using the same ids."""


class Written(BaseModel):
    id: int
    description: str


class WrittenBatch(BaseModel):
    items: list[Written]


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def copies_official_wording(description: str, official: str) -> bool:
    """True if most of the description's words appear in the official text."""
    mine = words(description)
    return bool(mine) and len(mine & words(official)) / len(mine) > 0.6


def sample_codes(db, count: int, rng: random.Random) -> list[HsnCode]:
    rows = [
        row
        for row in db.scalars(select(HsnCode))
        if (row.kind == "HSN" and len(row.code) == 8 or row.kind == "SAC" and len(row.code) == 6)
        and not re.fullmatch(r"(?i)\W*others?\W*", row.description)
        and len(words(row.description)) >= 2
    ]
    by_chapter: dict[str, list[HsnCode]] = {}
    for row in rows:
        by_chapter.setdefault(row.chapter, []).append(row)
    # Round-robin over chapters so every part of the list is represented.
    chapters = sorted(by_chapter)
    for chapter in chapters:
        rng.shuffle(by_chapter[chapter])
    picked: list[HsnCode] = []
    while len(picked) < count and any(by_chapter.values()):
        for chapter in chapters:
            if by_chapter[chapter] and len(picked) < count:
                picked.append(by_chapter[chapter].pop())
    return picked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    provider = get_provider()
    if provider is None:
        sys.exit("LLM_API_KEY is not set.")
    model = get_settings().llm_model_fast
    db = SessionLocal()
    codes = sample_codes(db, args.count, random.Random(args.seed))
    print(f"Sampled {len(codes)} codes from {len({c.chapter for c in codes})} chapters")

    kept, dropped = [], 0
    for start in range(0, len(codes), 20):  # 20 codes per call keeps free-tier calls low
        batch = codes[start : start + 20]
        listing = "\n".join(
            f"{i}. code {c.code} ({c.kind}): {c.description}"
            + (f" [heading: {c.heading}]" if c.heading else "")
            for i, c in enumerate(batch)
        )
        result = provider.generate_json(
            model=model,
            system=PROMPT,
            images=[],
            instruction=listing,
            output_type=WrittenBatch,
            effort="low",
        )
        written = {
            item.id: item.description.strip()
            for item in WrittenBatch.model_validate_json(result.text or '{"items": []}').items
        }
        for i, code in enumerate(batch):
            description = written.get(i, "")
            official = f"{code.description} {code.heading or ''}"
            if not description or copies_official_wording(description, official):
                dropped += 1
                continue
            kept.append(
                {
                    "id": f"hsn_{len(kept) + 1:03d}",
                    "description": description,
                    "code": code.code,
                    "heading": code.code[:4],
                    "kind": code.kind,
                    "official": code.description,
                }
            )
        print(f"  {min(start + 20, len(codes))}/{len(codes)} done, kept {len(kept)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept))
    print(f"Wrote {len(kept)} items to {OUT.relative_to(REPO)} ({dropped} dropped as copies/empty)")


if __name__ == "__main__":
    main()
