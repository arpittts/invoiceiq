"""Application settings, loaded from environment variables or a .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Look for .env in backend/ first, then the repo root. Real env vars always win.
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+psycopg://invoiceiq:invoiceiq@localhost:5432/invoiceiq"
    allowed_origins: str = "http://localhost:5173"

    # Auth. No default for the secret: the app refuses to start without one.
    jwt_secret: str
    jwt_expire_minutes: int = 60

    # Uploads
    storage_backend: Literal["local", "db"] = "local"
    upload_dir: str = "uploads"
    max_upload_mb: int = 10
    max_pages: int = 10

    # LLM. The key is optional so the app (and tests) run without one;
    # extraction marks the invoice "failed" with a clear message instead.
    # "gemini" (free tier, default) or "anthropic". LLM_API_KEY is that vendor's key.
    llm_provider: Literal["gemini", "anthropic"] = "gemini"
    llm_api_key: str = ""
    # Flash-Lite: the free tier allows only ~20 requests/day on gemini-3.8-flash.
    llm_model_extract: str = "gemini-3.5-flash-lite"
    llm_model_fast: str = "gemini-3.5-flash-lite"
    # Claude only: reasoning effort for extraction.
    llm_effort_extract: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # Global kill switch for all LLM work (blueprint section 10).
    extraction_enabled: bool = True
    # Schema-validation retries after the first attempt.
    extraction_max_retries: int = 2
    # Longest side of a page image sent to the model, in pixels.
    render_max_edge: int = 2000

    # Validation: warn when an invoice is older than this (GST input-credit window)
    max_invoice_age_days: int = 730

    # Tracing (optional; tracing is skipped when the keys are empty)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        # Hosts like Neon hand out "postgresql://..." or "postgres://..." URLs.
        # SQLAlchemy needs the driver named explicitly, so add "+psycopg".
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
