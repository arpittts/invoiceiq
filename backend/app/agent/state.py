"""What the invoice-processing graph carries from node to node (blueprint section 10)."""

from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

from sqlalchemy.orm import Session

from app.llm.embeddings import Embedder
from app.llm.provider import LLMProvider
from app.schemas.extraction import InvoiceExtraction
from app.services.extraction import Attempt
from app.services.pdf import PageImage
from app.services.validation import Issue


class AgentState(TypedDict, total=False):
    invoice_id: int
    pages: list[PageImage]
    extraction: InvoiceExtraction | None
    issues: list[Issue]  # from the latest validation
    attempts: list[Attempt]  # every LLM extraction call, saved as extraction_runs
    re_extractions: int
    tokens_used: int
    cost_usd: Decimal
    decision: str  # "auto_approve", "review" or "failed"
    reason: str  # why, in one sentence (shown in traces and logs)
    path: list[str]  # node names in the order they ran


@dataclass
class AgentDeps:
    """Things the nodes need that are not part of the state (and not serialisable)."""

    db: Session
    provider: LLMProvider
    embedder: Embedder
