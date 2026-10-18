"""The steps of the invoice-processing graph (blueprint section 10).

Each node takes the current state (and AgentDeps) and returns only the keys it
changes. Routing functions look at the state and name the next node. All the
real work lives in the services; the nodes just decide what happens next.
"""

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from app.agent.state import AgentDeps, AgentState
from app.config import get_settings
from app.models import Invoice, InvoiceStatus, ReviewEvent
from app.schemas.extraction import InvoiceExtraction
from app.services.classification import classify_invoice
from app.services.extraction import apply_extraction, extract_from_pages, save_runs
from app.services.validation import InvoiceData, india_today, save_validation, validate

logger = logging.getLogger(__name__)


def _spend(state: AgentState, attempts) -> dict:
    """Token and cost totals after some new LLM attempts."""
    return {
        "attempts": state.get("attempts", []) + attempts,
        "tokens_used": state.get("tokens_used", 0)
        + sum(a.input_tokens + a.output_tokens for a in attempts),
        "cost_usd": state.get("cost_usd", Decimal(0))
        + sum((a.cost_usd or Decimal(0)) for a in attempts),
    }


def _ran(state: AgentState, node: str) -> list[str]:
    return state.get("path", []) + [node]


def within_budget(state: AgentState) -> bool:
    settings = get_settings()
    return state.get("tokens_used", 0) < settings.max_tokens_per_invoice and state.get(
        "cost_usd", Decimal(0)
    ) < Decimal(str(settings.max_cost_per_invoice_usd))


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def extract(state: AgentState, deps: AgentDeps) -> dict:
    """Read the page images with the vision LLM (schema retries happen inside)."""
    settings = get_settings()
    outcome = extract_from_pages(
        state["pages"],
        deps.provider,
        model=settings.llm_model_extract,
        effort=settings.llm_effort_extract,
        max_retries=settings.extraction_max_retries,
        trace_metadata={"invoice_id": state["invoice_id"]},
    )
    update = {**_spend(state, outcome.attempts), "path": _ran(state, "extract")}
    update["extraction"] = outcome.extraction
    if outcome.extraction is None:
        update.update(decision="failed", reason=outcome.error or "Extraction failed")
    return update


def check(state: AgentState, deps: AgentDeps) -> dict:
    """Run the deterministic rules on the current read (before anything is saved)."""
    settings = get_settings()
    issues = validate(
        InvoiceData.from_extraction(state["extraction"]),
        today=india_today(),
        max_age_days=settings.max_invoice_age_days,
    )
    return {"issues": issues, "path": _ran(state, "validate")}


def focus_instruction(issues) -> str:
    """Tell the model exactly what our validator rejected, field by field."""
    problems = "\n".join(f"- {i.field}: {i.message}" for i in issues if i.severity == "error")
    return (
        "A first reading of this invoice failed these checks:\n"
        f"{problems}\n"
        "Look again closely at those fields on the page, character by character, and "
        "report them exactly as printed. If the printed numbers really are inconsistent, "
        "copy them as printed anyway: do not correct the invoice."
    )


def merge_fields(old: InvoiceExtraction, new: InvoiceExtraction, issues) -> InvoiceExtraction:
    """Keep the first read, replacing only what the failing checks point at.

    A field that was already fine can't get worse on a second read this way.
    """
    data, fresh = old.model_dump(), new.model_dump()
    header_fields = set(InvoiceExtraction.model_fields) - {"line_items"}
    lines_to_replace: set[int] = set()
    replace_all_lines = False
    for issue in (i for i in issues if i.severity == "error"):
        line = re.match(r"line_items\[(\d+)\]", issue.field)
        if line:
            lines_to_replace.add(int(line.group(1)))
        elif issue.field in header_fields:
            data[issue.field] = fresh[issue.field]
        if issue.rule_code == "TAX_TYPE":  # the place of supply or tax columns were misread
            for name in ("place_of_supply", "cgst", "sgst", "igst"):
                data[name] = fresh[name]
        if issue.rule_code in ("TOTAL_MATH", "TAX_MATH") and not line:
            # A line was skipped or invented: the line count itself is wrong.
            replace_all_lines |= len(fresh["line_items"]) != len(data["line_items"])
    if replace_all_lines:
        data["line_items"] = fresh["line_items"]
    else:
        for number in lines_to_replace:
            if number <= len(fresh["line_items"]) and number <= len(data["line_items"]):
                data["line_items"][number - 1] = fresh["line_items"][number - 1]
    return InvoiceExtraction.model_validate(data)


def re_extract(state: AgentState, deps: AgentDeps) -> dict:
    """A targeted second read focused on the fields that failed validation."""
    settings = get_settings()
    outcome = extract_from_pages(
        state["pages"],
        deps.provider,
        model=settings.llm_model_extract,
        effort=settings.llm_effort_extract,
        max_retries=settings.extraction_max_retries,
        trace_metadata={"invoice_id": state["invoice_id"], "re_extraction": True},
        focus=focus_instruction(state["issues"]),
        first_attempt=len(state.get("attempts", [])) + 1,
    )
    update = {
        **_spend(state, outcome.attempts),
        "re_extractions": state.get("re_extractions", 0) + 1,
        "path": _ran(state, "re_extract"),
    }
    if outcome.extraction is not None:
        update["extraction"] = merge_fields(
            state["extraction"], outcome.extraction, state["issues"]
        )
    return update


def save(state: AgentState, deps: AgentDeps) -> dict:
    """Write the (possibly merged) extraction onto the invoice and its line items."""
    invoice = deps.db.get(Invoice, state["invoice_id"])
    apply_extraction(invoice, state["extraction"])
    deps.db.flush()
    return {"path": _ran(state, "save")}


def classify(state: AgentState, deps: AgentDeps) -> dict:
    """Suggest HSN/SAC codes, once, now that the numbers are settled."""
    if get_settings().classification_enabled:
        invoice = deps.db.get(Invoice, state["invoice_id"])
        try:
            classify_invoice(deps.db, invoice, deps.provider, deps.embedder)
        except Exception:
            logger.exception("HSN classification failed for invoice %s", invoice.id)
    return {"path": _ran(state, "classify")}


def final_check(state: AgentState, deps: AgentDeps) -> dict:
    """All rules on the saved invoice, now including duplicates and HSN checks."""
    invoice = deps.db.get(Invoice, state["invoice_id"])
    issues = save_validation(
        deps.db, invoice, today=india_today(), max_age_days=get_settings().max_invoice_age_days
    )
    return {"issues": issues, "path": _ran(state, "final_check")}


def decide(state: AgentState, deps: AgentDeps) -> dict:
    """Auto-approve only a spotless, confident invoice, and only when switched on."""
    settings = get_settings()
    invoice = deps.db.get(Invoice, state["invoice_id"])
    issues = state.get("issues", [])
    weak_lines = [
        line.position
        for line in invoice.line_items
        if line.hsn_source_id is None
        or (line.hsn_confidence or 0) < settings.auto_approve_min_hsn_confidence
    ]
    if not settings.auto_approve_enabled:
        decision, reason = "review", "Auto-approve is off: a person reviews every invoice"
    elif issues:
        decision, reason = "review", f"{len(issues)} validation issue(s) need a person"
    elif (invoice.overall_confidence or 0) < settings.auto_approve_min_confidence:
        decision, reason = "review", f"Confidence {invoice.overall_confidence} is below the bar"
    elif weak_lines:
        decision, reason = "review", f"No confident HSN/SAC code on line(s) {weak_lines}"
    else:
        decision, reason = "auto_approve", "No issues, high confidence, all codes found"
    return {"decision": decision, "reason": reason, "path": _ran(state, "decide")}


def finalize(state: AgentState, deps: AgentDeps) -> dict:
    """Save every LLM attempt and set the final status."""
    db = deps.db
    invoice = db.get(Invoice, state["invoice_id"])
    save_runs(db, invoice.id, state.get("attempts", []))
    decision = state.get("decision", "review")
    if decision == "failed":
        invoice.status = InvoiceStatus.FAILED
    elif decision == "auto_approve":
        invoice.status = InvoiceStatus.APPROVED
        invoice.approved_at = datetime.now(UTC)
        db.add(
            ReviewEvent(
                invoice_id=invoice.id,
                user_id=invoice.user_id,
                field="status",
                old_value=InvoiceStatus.PROCESSING,
                new_value="approved_by_agent",
            )
        )
    else:
        invoice.status = InvoiceStatus.NEEDS_REVIEW
    return {"path": _ran(state, "finalize")}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def after_extract(state: AgentState) -> str:
    return "finalize" if state.get("extraction") is None else "validate"


def after_validate(state: AgentState) -> str:
    """Re-read only for errors, at most N times, and only while within budget."""
    errors = [issue for issue in state.get("issues", []) if issue.severity == "error"]
    if (
        errors
        and state.get("re_extractions", 0) < get_settings().max_reextractions
        and within_budget(state)
    ):
        return "re_extract"
    return "save"
