from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.llm.embeddings import get_embedder
from app.schemas.invoice import HsnCodeResponse
from app.services.classification import search

router = APIRouter(prefix="/hsn", tags=["hsn"])


@router.get("/search", response_model=list[HsnCodeResponse])
def search_codes(
    user: CurrentUser,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid",
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
):
    """Look up official HSN/SAC codes for a product or service description."""
    return search(db, get_embedder(), q, mode=mode, limit=limit)
