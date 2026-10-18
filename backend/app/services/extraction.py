"""Invoice extraction: page images -> LLM -> validated InvoiceExtraction -> database.

Two layers, so the logic can be tested and reused without FastAPI:
- `extract_from_pages` is pure: images in, outcome out (no database).
- `process_invoice` loads an invoice, runs extraction, and saves the result.
  It runs as a background task after upload today and can move to a queue later.
"""

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.llm.embeddings import get_embedder
from app.llm.pricing import cost_usd
from app.llm.provider import AnthropicProvider, GeminiProvider, LLMProvider
from app.models import ExtractionRun, Invoice, InvoiceStatus, LineItem
from app.schemas.extraction import ExtractionOutput, InvoiceExtraction, to_invoice_extraction
from app.services.classification import classify_invoice
from app.services.pdf import PageImage, render_pages
from app.services.storage import load_file
from app.services.validation import india_today, save_validation
from app.tracing import trace_generation, trace_span

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent.parent / "llm" / "prompts" / "extraction.md").read_text()
INSTRUCTION = "Extract the invoice shown in the page images above into the JSON schema."

# Issue codes stored in extraction_runs.issues_json
RETRYABLE = {"INVALID_JSON", "SCHEMA", "EMPTY"}


@dataclass
class Attempt:
    """What one LLM call produced. Becomes one extraction_runs row."""

    number: int
    model: str
    raw_json: dict | None
    issues: list[dict]
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    trace_id: str | None


@dataclass
class ExtractionOutcome:
    extraction: InvoiceExtraction | None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def error(self) -> str | None:
        if self.extraction is not None or not self.attempts:
            return None
        return "; ".join(issue["message"] for issue in self.attempts[-1].issues)


def _check_reply(text: str | None, stop_reason: str | None):
    """Validate one reply. Returns (raw_json, extraction or None, issues)."""
    if stop_reason == "refusal":
        return None, None, [{"code": "REFUSAL", "message": "The model declined the request"}]
    if stop_reason == "max_tokens":
        return None, None, [{"code": "TRUNCATED", "message": "The reply hit the token limit"}]
    if not text:
        return None, None, [{"code": "EMPTY", "message": "The model returned no text"}]
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        return None, None, [{"code": "INVALID_JSON", "message": f"Invalid JSON: {error}"}]
    try:
        extraction = to_invoice_extraction(ExtractionOutput.model_validate(raw))
    except ValueError as error:  # includes Pydantic's ValidationError
        return raw, None, [{"code": "SCHEMA", "message": str(error)[:1000]}]
    return raw, extraction, []


def extract_from_pages(
    pages: list[PageImage],
    provider: LLMProvider,
    *,
    model: str,
    effort: str,
    max_retries: int,
    trace_metadata: dict,
) -> ExtractionOutcome:
    """Call the model, validate, and retry with feedback when validation fails."""
    outcome = ExtractionOutcome(extraction=None)
    instruction = INSTRUCTION

    with trace_span("extract_invoice", metadata={**trace_metadata, "pages": len(pages)}):
        for number in range(1, max_retries + 2):
            with trace_generation(
                f"extraction attempt {number}", model, metadata={"attempt": number}
            ) as generation:
                try:
                    result = provider.generate_json(
                        model=model,
                        system=SYSTEM_PROMPT,
                        images=pages,
                        instruction=instruction,
                        output_type=ExtractionOutput,
                        effort=effort,
                    )
                except Exception as error:  # network/API errors after the SDK's own retries
                    logger.exception("LLM call failed")
                    outcome.attempts.append(
                        Attempt(
                            number=number,
                            model=model,
                            raw_json=None,
                            issues=[{"code": "API_ERROR", "message": str(error)[:500]}],
                            input_tokens=0,
                            output_tokens=0,
                            cost_usd=Decimal(0),
                            latency_ms=0,
                            trace_id=generation.trace_id,
                        )
                    )
                    generation.update(level="ERROR", status_message=str(error)[:500])
                    break

                raw, extraction, issues = _check_reply(result.text, result.stop_reason)
                cost = cost_usd(result.model, result.input_tokens, result.output_tokens)
                # Trace summary only: no invoice values leave our system (section 19).
                generation.update(
                    model=result.model,
                    output={
                        "valid": extraction is not None,
                        "issues": [issue["code"] for issue in issues],
                        "line_items": len(extraction.line_items) if extraction else None,
                        "overall_confidence": extraction.overall_confidence if extraction else None,
                    },
                    usage_details={"input": result.input_tokens, "output": result.output_tokens},
                    cost_details={"total": float(cost)} if cost is not None else None,
                    level="DEFAULT" if extraction else "WARNING",
                )

            outcome.attempts.append(
                Attempt(
                    number=number,
                    model=result.model,
                    raw_json=raw,
                    issues=issues,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=cost,
                    latency_ms=result.latency_ms,
                    trace_id=generation.trace_id,
                )
            )
            if extraction is not None:
                outcome.extraction = extraction
                break
            if not any(issue["code"] in RETRYABLE for issue in issues):
                break  # a refusal or truncation won't be fixed by asking again
            # Ask again, telling the model exactly what was wrong.
            instruction = (
                f"{INSTRUCTION}\n\nA previous answer for this invoice was rejected by our "
                f"validator: {issues[0]['message']}\n"
                "Read the pages again and return corrected JSON."
            )
    return outcome


def get_provider() -> LLMProvider | None:
    """The configured LLM vendor, or None when no API key is set."""
    settings = get_settings()
    if not settings.llm_api_key:
        return None
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings.llm_api_key)
    return GeminiProvider(settings.llm_api_key)


def _fit(value: str | None, length: int) -> str | None:
    """Keep strings within their column size; the full text stays in raw_json."""
    return value[:length] if value is not None else None


def _apply_extraction(invoice: Invoice, extraction: InvoiceExtraction) -> None:
    invoice.vendor_name = _fit(extraction.vendor_name.value, 255)
    invoice.vendor_gstin = _fit(extraction.vendor_gstin.value, 15)
    invoice.buyer_name = _fit(extraction.buyer_name.value, 255)
    invoice.buyer_gstin = _fit(extraction.buyer_gstin.value, 15)
    invoice.invoice_number = _fit(extraction.invoice_number.value, 100)
    invoice.invoice_date = extraction.invoice_date.value
    invoice.place_of_supply = _fit(extraction.place_of_supply.value, 100)
    invoice.subtotal = extraction.subtotal.value
    invoice.cgst = extraction.cgst.value
    invoice.sgst = extraction.sgst.value
    invoice.igst = extraction.igst.value
    invoice.total = extraction.total.value
    invoice.overall_confidence = extraction.overall_confidence
    invoice.line_items = [
        LineItem(
            position=position,
            description=item.description.value,
            hsn_sac_code=_fit(item.hsn_sac_code.value, 10),
            quantity=item.quantity.value,
            unit=_fit(item.unit.value, 20),
            unit_price=item.unit_price.value,
            discount=item.discount.value,
            taxable_value=item.taxable_value.value,
            gst_rate=item.gst_rate.value,
            cgst_amount=item.cgst_amount.value,
            sgst_amount=item.sgst_amount.value,
            igst_amount=item.igst_amount.value,
            line_total=item.line_total.value,
            confidence=min(getattr(item, name).confidence for name in type(item).model_fields),
        )
        for position, item in enumerate(extraction.line_items, start=1)
    ]


def process_invoice(invoice_id: int, provider: LLMProvider | None = None) -> None:
    """Extract one uploaded invoice and save the result. Safe to call from a background task."""
    settings = get_settings()
    if not settings.extraction_enabled:
        logger.info("Extraction disabled; invoice %s stays in processing", invoice_id)
        return

    db = SessionLocal()
    try:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None or invoice.status != InvoiceStatus.PROCESSING:
            return

        provider = provider or get_provider()
        if provider is None:
            db.add(
                ExtractionRun(
                    invoice_id=invoice.id,
                    attempt=1,
                    model=settings.llm_model_extract,
                    issues_json=[{"code": "NOT_CONFIGURED", "message": "LLM_API_KEY is not set"}],
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=Decimal(0),
                    latency_ms=0,
                )
            )
            invoice.status = InvoiceStatus.FAILED
            db.commit()
            return

        pages = render_pages(
            load_file(db, invoice.file), invoice.file.mime_type, settings.render_max_edge
        )
        outcome = extract_from_pages(
            pages,
            provider,
            model=settings.llm_model_extract,
            effort=settings.llm_effort_extract,
            max_retries=settings.extraction_max_retries,
            trace_metadata={"invoice_id": invoice.id},
        )

        runs = [
            ExtractionRun(
                invoice_id=invoice.id,
                attempt=a.number,
                model=a.model,
                raw_json=a.raw_json,
                issues_json=a.issues or None,
                input_tokens=a.input_tokens,
                output_tokens=a.output_tokens,
                cost_usd=a.cost_usd,
                latency_ms=a.latency_ms,
                trace_id=a.trace_id,
            )
            for a in outcome.attempts
        ]
        db.add_all(runs)

        if outcome.extraction is None:
            invoice.status = InvoiceStatus.FAILED
        else:
            _apply_extraction(invoice, outcome.extraction)
            if settings.classification_enabled:
                # Suggest HSN/SAC codes (Phase 5). Never blocks the invoice if it fails.
                try:
                    classify_invoice(db, invoice, provider, get_embedder())
                except Exception:
                    logger.exception("HSN classification failed for invoice %s", invoice.id)
            # Check the extracted values with the deterministic rules (Phase 3).
            # Issues don't block anything yet: a human reviews every invoice.
            save_validation(
                db, invoice, today=india_today(), max_age_days=settings.max_invoice_age_days
            )
            invoice.status = InvoiceStatus.NEEDS_REVIEW
        db.commit()
    except Exception:
        logger.exception("Processing invoice %s failed", invoice_id)
        db.rollback()
        invoice = db.get(Invoice, invoice_id)
        if invoice is not None:
            invoice.status = InvoiceStatus.FAILED
            db.commit()
    finally:
        db.close()
