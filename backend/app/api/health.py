from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import check_database

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> JSONResponse:
    """Liveness check used by Docker, the host platform, and the frontend."""
    db_ok = check_database()
    body = {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "unreachable"}
    return JSONResponse(body, status_code=200 if db_ok else 503)
