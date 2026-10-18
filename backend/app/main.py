"""FastAPI entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import check_database

app = FastAPI(title="InvoiceIQ API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
def health() -> JSONResponse:
    """Liveness check used by Docker, the host platform, and the frontend."""
    db_ok = check_database()
    body = {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "unreachable"}
    return JSONResponse(body, status_code=200 if db_ok else 503)
