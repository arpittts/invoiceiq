"""The MCP server's tools, called the way an MCP client would."""

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from app import mcp_server  # noqa: E402
from app.llm.provider import FakeProvider  # noqa: E402
from tests.conftest import register_and_login  # noqa: E402
from tests.test_classification import seed_corpus  # noqa: E402
from tests.test_extraction_service import valid_reply  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[2] / "data/sample_invoices/inv_001.pdf"


async def test_the_three_tools_are_listed_as_read_only():
    tools = {tool.name: tool for tool in await mcp_server.server.list_tools()}
    assert set(tools) == {"extract_invoice", "lookup_hsn", "query_ledger"}
    assert all(tool.annotations.read_only_hint for tool in tools.values())


def test_extract_invoice_reads_and_validates_without_saving(monkeypatch, db):
    from app.models import Invoice

    monkeypatch.setattr(mcp_server, "get_provider", lambda: FakeProvider([valid_reply()]))
    result = mcp_server.extract_invoice(str(SAMPLE))
    assert result["invoice"]["vendor_name"]["value"]
    assert isinstance(result["issues"], list) and result["attempts"] == 1
    assert db.query(Invoice).count() == 0  # nothing stored


@pytest.mark.parametrize(
    ("path", "message"), [("/no/such/file.pdf", "No such file"), (__file__, "Only .pdf")]
)
def test_extract_invoice_explains_bad_paths(path, message):
    with pytest.raises(ToolError, match=message):
        mcp_server.extract_invoice(path)


def test_lookup_hsn_returns_official_codes(db):
    seed_corpus(db)
    codes = mcp_server.lookup_hsn("brake pads for a car")
    assert codes and codes[0]["code"] == "87083000"
    assert {"code", "description", "default_gst_rate", "source_note"} <= set(codes[0])


def test_query_ledger_needs_an_account(monkeypatch):
    monkeypatch.delenv("MCP_USER_EMAIL", raising=False)
    with pytest.raises(ToolError, match="MCP_USER_EMAIL"):
        mcp_server.query_ledger("How much did I spend?")


def test_query_ledger_answers_for_that_account(client, monkeypatch):
    register_and_login(client, "owner@example.com")
    monkeypatch.setenv("MCP_USER_EMAIL", "Owner@example.com")
    provider = FakeProvider(
        [
            json.dumps({"sql": "SELECT COUNT(*) AS n FROM v_invoices", "reason": "count"}),
            json.dumps({"answer": "You have no approved invoices yet."}),
        ]
    )
    monkeypatch.setattr(mcp_server, "get_provider", lambda: provider)
    result = mcp_server.query_ledger("How many invoices?")
    assert result["rows"] == [[0]] and result["error"] is None
    assert result["answer"].startswith("You have")
