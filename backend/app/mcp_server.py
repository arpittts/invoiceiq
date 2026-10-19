"""InvoiceIQ as an MCP server (blueprint section 12), for MCP clients such as Claude.

Three read-only tools, reusing the same services as the web API:
- extract_invoice(file_path): read a local PDF/PNG/JPEG invoice and check it
- lookup_hsn(description): official HSN/SAC codes for a product or service
- query_ledger(question): a plain-English question about one user's approved invoices

It runs locally over stdio, next to your own database; it is not deployed.
Nothing is saved: extract_invoice returns the reading and the validation issues
without adding the invoice to anyone's account. query_ledger answers for the
account named by MCP_USER_EMAIL, with the same read-only role, SQL guard and
user isolation as the Ask page. Real model calls count against the daily cap.

Run (from backend/):  .venv/bin/python -m app.mcp_server
"""

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# Expected failures: the client sees the message (anything else is reported as a crash).
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.llm.embeddings import get_embedder
from app.models import User
from app.schemas.invoice import HsnCodeResponse
from app.services import ledger_qa
from app.services.classification import search
from app.services.extraction import extract_from_pages, get_provider
from app.services.pdf import render_pages
from app.services.validation import InvoiceData, india_today, validate

MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

server = MCPServer(
    name="invoiceiq",
    instructions=(
        "Tools for Indian GST invoices: read an invoice file, look up HSN/SAC codes, and "
        "answer questions about the user's approved invoices. Outputs are an automation aid, "
        "not tax advice; rates and codes change, so verify before filing."
    ),
)


def _provider():
    provider = get_provider()
    if provider is None:
        raise ToolError("LLM_API_KEY is not set in the server's environment.")
    return provider


@server.tool(annotations=READ_ONLY)
def extract_invoice(file_path: str) -> dict:
    """Read a GST invoice (PDF, PNG or JPEG) and check it with the validation rules.

    Returns every field exactly as printed (with the model's confidence) and the
    validation issues found. The invoice is not saved anywhere.
    """
    settings = get_settings()
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise ToolError(f"No such file: {path}")
    mime_type = MIME.get(path.suffix.lower())
    if mime_type is None:
        raise ToolError("Only .pdf, .png, .jpg and .jpeg files are supported.")
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise ToolError(f"The file is larger than {settings.max_upload_mb} MB.")
    pages = render_pages(path.read_bytes(), mime_type, settings.render_max_edge)
    if len(pages) > settings.max_pages:
        raise ToolError(f"The file has more than {settings.max_pages} pages.")

    outcome = extract_from_pages(
        pages,
        _provider(),
        model=settings.llm_model_extract,
        effort=settings.llm_effort_extract,
        max_retries=settings.extraction_max_retries,
        trace_metadata={"source": "mcp"},
    )
    if outcome.extraction is None:
        raise ToolError(outcome.error or "The invoice could not be read.")
    issues = validate(
        InvoiceData.from_extraction(outcome.extraction),
        today=india_today(),
        max_age_days=settings.max_invoice_age_days,
    )
    return {
        "invoice": outcome.extraction.model_dump(mode="json"),
        "issues": [
            {"rule": i.rule_code, "field": i.field, "severity": i.severity, "message": i.message}
            for i in issues
        ],
        "attempts": len(outcome.attempts),
    }


@server.tool(annotations=READ_ONLY)
def lookup_hsn(description: str, limit: int = 5) -> list[dict]:
    """Official HSN/SAC codes for a product or service description (hybrid search).

    Default GST rates are indicative: some codes have conditional rates.
    """
    if len(description.strip()) < 2:
        raise ToolError("Describe the product or service in at least two characters.")
    limit = max(1, min(limit, 20))
    with SessionLocal() as db:
        codes = search(db, get_embedder(), description, mode="hybrid", limit=limit)
        return [HsnCodeResponse.model_validate(code).model_dump(mode="json") for code in codes]


@server.tool(annotations=READ_ONLY)
def query_ledger(question: str) -> dict:
    """Answer a plain-English question about the approved invoices of MCP_USER_EMAIL's account.

    Returns the answer with the SQL that produced it and the rows, so it can be checked.
    """
    email = os.environ.get("MCP_USER_EMAIL", "").strip().lower()
    if not email:
        raise ToolError("Set MCP_USER_EMAIL to the account whose ledger should be queried.")
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        raise ToolError(f"No account with the email {email}.")
    result = ledger_qa.ask(_provider(), question.strip(), user_id)
    return {
        "answer": result.answer,
        "sql": result.sql,
        "columns": result.columns,
        "rows": result.rows,
        "error": result.error,
    }


if __name__ == "__main__":
    server.run("stdio")
