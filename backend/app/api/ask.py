from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import CurrentUser
from app.services import ledger_qa
from app.services.extraction import get_provider

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AskResponse(BaseModel):
    question: str
    answer: str | None
    sql: str | None
    columns: list[str]
    rows: list[list]
    error: str | None


@router.post("/ask", response_model=AskResponse)
def ask_ledger(body: AskRequest, user: CurrentUser) -> AskResponse:
    """Ask a plain-English question about your approved invoices."""
    provider = get_provider()
    if provider is None:
        return AskResponse(
            question=body.question,
            answer=None,
            sql=None,
            columns=[],
            rows=[],
            error="LLM_API_KEY is not set on the server.",
        )
    result = ledger_qa.ask(provider, body.question.strip(), user.id)
    return AskResponse(**result.__dict__)
