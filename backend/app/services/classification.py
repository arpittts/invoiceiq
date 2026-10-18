"""HSN/SAC code lookup for invoice lines: hybrid retrieval + LLM selection (blueprint section 9).

1. Retrieve: three ranked lists of official codes for each line description:
   - vector: cosine similarity of embeddings (pgvector), good at meaning ("bike" ~ "motorcycle")
   - keyword: Postgres full-text search, good at exact terms ("LED", "cement")
   - printed code: official codes that start with the HSN/SAC printed on the invoice
2. Fuse: reciprocal rank fusion (RRF) merges the lists without having to compare
   their very different scores: each code gets sum(1 / (k + rank)) over the lists.
3. Select: one LLM call for the whole invoice picks the best of each line's top 5,
   and must cite the candidate it chose. Low confidence means "leave it to a human".
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.embeddings import Embedder
from app.llm.provider import LLMProvider
from app.models import HsnCode, Invoice
from app.tracing import trace_generation

logger = logging.getLogger(__name__)

RRF_K = 60  # the standard constant from the original RRF paper; dampens top-rank dominance
RETRIEVE_EACH = 20  # candidates taken from each retriever before fusion
CANDIDATES_FOR_LLM = 5

SELECTION_PROMPT = (
    Path(__file__).parent.parent / "llm" / "prompts" / "hsn_selection.md"
).read_text()


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

# Words that describe the quantity or packing, not the product. In "Basmati rice
# 25kg bag" the word "bag" made keyword search return handbags. Measured: removing
# them (and numbers) improved retrieval on the labelled set; see the README table.
_PACKING_WORDS = {
    "bag",
    "bags",
    "pack",
    "packs",
    "packet",
    "packets",
    "box",
    "boxes",
    "bottle",
    "bottles",
    "tin",
    "tins",
    "jar",
    "jars",
    "roll",
    "rolls",
    "piece",
    "pieces",
    "pcs",
    "pc",
    "nos",
    "set",
    "sets",
    "bulk",
    "drum",
    "drums",
    "carton",
    "cartons",
    "pouch",
    "sachet",
    "tube",
    "can",
    "cans",
    "case",
    "unit",
    "units",
    "qty",
    "supply",
    "kg",
    "kgs",
    "gm",
    "gms",
    "g",
    "ml",
    "l",
    "ltr",
    "litre",
    "litres",
    "liter",
    "w",
    "watt",
    "v",
    "mm",
    "cm",
    "m",
    "inch",
}


def clean_query(text: str) -> str:
    """'Basmati Rice 25kg bag' -> 'basmati rice': drop numbers, units and packing words."""
    text = re.sub(r"\d+(\.\d+)?\s*[a-z]{0,6}\b", " ", text.lower())  # 25kg, 9w, 500 ml
    # Keep hyphenated words whole: Postgres indexes "T-SHIRTS" as "t-shirt", so
    # splitting it into "t" + "shirt" made both keyword and vector search miss.
    words = [w for w in re.findall(r"[a-z]+(?:-[a-z]+)*", text) if w not in _PACKING_WORDS]
    return " ".join(words)


# 2-digit chapter rows ("50 SILK") are too broad to be an invoice code.
_NOT_A_CHAPTER = func.length(HsnCode.code) > 2


def vector_ranking(db: Session, query_vector: list[float], limit: int = RETRIEVE_EACH) -> list[int]:
    """Code ids, most similar first (<=> is pgvector's cosine distance)."""
    return list(
        db.scalars(
            select(HsnCode.id)
            .where(HsnCode.embedding.is_not(None), _NOT_A_CHAPTER)
            .order_by(HsnCode.embedding.cosine_distance(query_vector))
            .limit(limit)
        )
    )


def keyword_ranking(db: Session, query: str, limit: int = RETRIEVE_EACH) -> list[int]:
    """Code ids ranked by full-text match: first by how many of the query's words a
    code contains, then by Postgres's ts_rank. Words are OR-ed, so one match is enough
    to be a candidate. (Ranking by ts_rank alone let one repeated common word win.)"""
    words = clean_query(query).split()
    if not words:
        return []
    # One tsquery per word (plainto_tsquery handles hyphens and stemming), OR-ed with ||.
    per_word = [func.plainto_tsquery("english", word) for word in words]
    tsquery = per_word[0]
    for word_query in per_word[1:]:
        tsquery = tsquery.op("||")(word_query)
    matched_words = sum(
        case((HsnCode.tsv.op("@@")(word_query), 1), else_=0) for word_query in per_word
    )
    return list(
        db.scalars(
            select(HsnCode.id)
            .where(HsnCode.tsv.op("@@")(tsquery), _NOT_A_CHAPTER)
            .order_by(
                matched_words.desc(), func.ts_rank_cd(HsnCode.tsv, tsquery).desc(), HsnCode.code
            )
            .limit(limit)
        )
    )


def printed_code_ranking(db: Session, printed: str | None, limit: int = RETRIEVE_EACH) -> list[int]:
    """Official codes starting with the printed code, most general (shortest) first."""
    digits = re.sub(r"\D", "", printed or "")
    if len(digits) < 4:
        return []
    return list(
        db.scalars(
            select(HsnCode.id)
            .where(HsnCode.code.startswith(digits[:8]))
            .order_by(func.length(HsnCode.code), HsnCode.code)
            .limit(limit)
        )
    )


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Merge ranked lists: score(id) = sum over lists of 1 / (k + rank), rank starting at 1."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def search(
    db: Session,
    embedder: Embedder,
    query: str,
    *,
    printed_code: str | None = None,
    mode: str = "hybrid",
    limit: int = CANDIDATES_FOR_LLM,
) -> list[HsnCode]:
    """Top codes for a description. `mode` is hybrid, vector or keyword (for the comparison)."""
    rankings = []
    if mode in ("hybrid", "vector"):
        rankings.append(vector_ranking(db, embedder.embed_query(clean_query(query) or query)))
    if mode in ("hybrid", "keyword"):
        rankings.append(keyword_ranking(db, query))
    if mode == "hybrid":
        rankings.append(printed_code_ranking(db, printed_code))
    ids = [code_id for code_id, _ in reciprocal_rank_fusion(rankings)[:limit]]
    rows = {row.id: row for row in db.scalars(select(HsnCode).where(HsnCode.id.in_(ids)))}
    return [rows[code_id] for code_id in ids if code_id in rows]


def corpus_is_empty(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(HsnCode)) == 0


# ---------------------------------------------------------------------------
# Selection by the LLM
# ---------------------------------------------------------------------------


class LineChoice(BaseModel):
    line: int
    candidate_id: int | None = Field(description="The chosen candidate's id, or null if none fits.")
    confidence: float = Field(description="Probability the choice is correct, 0.0 to 1.0.")
    reason: str = Field(description="One short sentence naming the chosen candidate id.")


class SelectionOutput(BaseModel):
    choices: list[LineChoice]


@dataclass
class Suggestion:
    code: HsnCode | None
    confidence: float | None
    reason: str


def select_codes(
    provider: LLMProvider, model: str, lines: list[dict], candidates: dict[int, list[HsnCode]]
) -> dict[int, Suggestion]:
    """One LLM call for all lines. Returns line number -> suggestion; never trusts an uncited id."""
    payload = [
        {
            "line": line["line"],
            "description": line["description"],
            "printed_code": line["printed_code"],
            "candidates": [
                {"id": c.id, "code": c.code, "description": c.description, "heading": c.heading}
                for c in candidates[line["line"]]
            ],
        }
        for line in lines
    ]
    with trace_generation("hsn selection", model, metadata={"lines": len(lines)}) as generation:
        result = provider.generate_json(
            model=model,
            system=SELECTION_PROMPT,
            images=[],
            instruction=json.dumps(payload, ensure_ascii=False),
            output_type=SelectionOutput,
            effort="low",
        )
        generation.update(
            model=result.model,
            usage_details={"input": result.input_tokens, "output": result.output_tokens},
        )

    parsed = SelectionOutput.model_validate_json(result.text or "")
    min_confidence = get_settings().hsn_min_confidence
    suggestions: dict[int, Suggestion] = {}
    for choice in parsed.choices:
        allowed = {c.id: c for c in candidates.get(choice.line, [])}
        chosen = allowed.get(choice.candidate_id) if choice.candidate_id is not None else None
        confidence = min(max(choice.confidence, 0.0), 1.0)
        if chosen is None:
            reason = (
                choice.reason if choice.candidate_id is None else "Model cited an unknown candidate"
            )
            suggestions[choice.line] = Suggestion(None, confidence, reason)
        elif confidence < min_confidence:
            suggestions[choice.line] = Suggestion(
                None, confidence, f"Not sure enough to suggest a code: {choice.reason}"
            )
        else:
            suggestions[choice.line] = Suggestion(chosen, confidence, choice.reason)
    return suggestions


def classify_invoice(
    db: Session,
    invoice: Invoice,
    provider: LLMProvider,
    embedder: Embedder,
    *,
    only_unclassified: bool = False,
) -> int:
    """Suggest HSN/SAC codes for an invoice's lines. Returns how many lines were classified.

    Failures are logged, not raised: a missing suggestion must never block a review.
    """
    if corpus_is_empty(db):
        return 0
    lines, candidates = [], {}
    for item in invoice.line_items:
        if not item.description or (only_unclassified and item.hsn_confidence is not None):
            continue
        found = search(db, embedder, item.description, printed_code=item.hsn_sac_code)
        if found:
            lines.append(
                {
                    "line": item.position,
                    "description": item.description,
                    "printed_code": item.hsn_sac_code,
                }
            )
            candidates[item.position] = found
    if not lines:
        return 0
    try:
        suggestions = select_codes(provider, get_settings().llm_model_fast, lines, candidates)
    except Exception:
        logger.exception("HSN selection failed for invoice %s", invoice.id)
        return 0

    for item in invoice.line_items:
        suggestion = suggestions.get(item.position)
        if suggestion is None:
            continue
        code = suggestion.code
        item.hsn_source_id = code.id if code else None
        item.suggested_hsn_code = code.code if code else None
        item.suggested_gst_rate = code.default_gst_rate if code else None
        item.hsn_confidence = suggestion.confidence
        item.hsn_reason = suggestion.reason[:500]
    return len(suggestions)
