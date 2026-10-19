"""Shared test setup.

Tests run against a separate database (invoiceiq_test) so they never touch dev data.
Environment variables are set *before* the app is imported, because settings are read once.
"""

import os
import tempfile
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://invoiceiq:invoiceiq@localhost:5432/invoiceiq_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-signing"
os.environ["STORAGE_BACKEND"] = "local"
# No real LLM calls or traces from tests. Extraction tests switch extraction on
# and inject a FakeProvider themselves.
os.environ["EXTRACTION_ENABLED"] = "false"
os.environ["LLM_API_KEY"] = ""
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="invoiceiq-test-uploads-")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.llm.embeddings import HashingEmbedder  # noqa: E402
from app.llm.provider import FakeProvider  # noqa: E402
from app.main import app  # noqa: E402
from app.services import extraction  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Build the schema with the real migrations, so the migrations themselves are tested."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    from app.ratelimit import limiter

    limiter.reset()
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, invoice_files, file_blobs, invoices, line_items, extraction_runs, "
                "validation_issues, review_events, hsn_codes, ledger_query_context, "
                "llm_usage_daily "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def register_and_login(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    credentials = {"email": email, "password": "correct-horse-battery"}
    client.post("/api/v1/auth/register", json=credentials)
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    return register_and_login(client)


@pytest.fixture
def extraction_on(monkeypatch):
    """Turn extraction on and route it to a FakeProvider the test controls."""
    monkeypatch.setattr(get_settings(), "extraction_enabled", True)
    provider = FakeProvider([], model="claude-opus-5-5")
    from app.services import review

    for module in (extraction, review):  # both look the provider up by name
        monkeypatch.setattr(module, "get_provider", lambda: provider)
    return provider


@pytest.fixture(autouse=True)
def hashing_embedder(monkeypatch):
    """Never load the real 130 MB model in tests: use the word-hashing stand-in everywhere."""
    from app.api import hsn
    from app.services import review

    embedder = HashingEmbedder()
    for module in (extraction, review, hsn):
        monkeypatch.setattr(module, "get_embedder", lambda: embedder)
    return embedder
