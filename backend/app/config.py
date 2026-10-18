"""Application settings, loaded from environment variables or a .env file."""

from functools import lru_cache
from typing import Literal

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

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
