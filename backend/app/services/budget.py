"""The daily cap on real model calls and spend (blueprint sections 18-19).

Every real LLM call first reserves one call in llm_usage_daily for today (India
time). The reservation is a single atomic UPDATE that only succeeds while both
limits still have room, so two requests at once can't push past the cap. When
the cap is reached, LLM work stops with a clear message until midnight IST;
the demo invoices still work, because they replay recorded replies.

On Gemini's free tier every call costs $0, so the call limit is what protects
the free daily quota; the dollar limit matters once a paid model is used.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.llm.pricing import cost_usd
from app.llm.provider import LLMProvider, LLMResult
from app.services.validation import india_today

CAP_MESSAGE = (
    "The demo's daily AI limit is reached, so new invoices can't be read until midnight "
    "India time. The demo invoices still work."
)


class DailyCapReached(Exception):
    def __init__(self) -> None:
        super().__init__(CAP_MESSAGE)


@dataclass
class Usage:
    day: date
    calls: int
    call_limit: int
    cost_usd: Decimal
    budget_usd: Decimal

    @property
    def cap_reached(self) -> bool:
        return self.calls >= self.call_limit or self.cost_usd >= self.budget_usd


def usage_today() -> Usage:
    settings = get_settings()
    today = india_today()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT calls, cost_usd FROM llm_usage_daily WHERE day = :day"), {"day": today}
        ).first()
    calls, cost = row if row else (0, Decimal(0))
    return Usage(
        day=today,
        calls=calls,
        call_limit=settings.daily_llm_call_limit,
        cost_usd=Decimal(cost),
        budget_usd=Decimal(str(settings.daily_budget_usd)),
    )


def ensure_available() -> None:
    """Raise DailyCapReached before starting LLM work that can't finish today."""
    if usage_today().cap_reached:
        raise DailyCapReached


def reserve_call() -> None:
    """Count one model call against today's cap, or raise if there is no room left."""
    settings = get_settings()
    with engine.begin() as conn:
        reserved = conn.execute(
            text(
                "INSERT INTO llm_usage_daily (day, calls, cost_usd) "
                "SELECT :day, 1, 0 WHERE :limit > 0 AND :budget > 0 "
                "ON CONFLICT (day) DO UPDATE SET calls = llm_usage_daily.calls + 1 "
                "WHERE llm_usage_daily.calls < :limit AND llm_usage_daily.cost_usd < :budget "
                "RETURNING calls"
            ),
            {
                "day": india_today(),
                "limit": settings.daily_llm_call_limit,
                "budget": settings.daily_budget_usd,
            },
        ).first()
    if reserved is None:
        raise DailyCapReached


def add_cost(amount: Decimal) -> None:
    if amount:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE llm_usage_daily SET cost_usd = cost_usd + :amount WHERE day = :day"),
                {"amount": amount, "day": india_today()},
            )


@dataclass
class BudgetedProvider:
    """Wraps the real provider: every call is counted, and refused once the cap is hit."""

    inner: LLMProvider

    def generate_json(self, **kwargs) -> LLMResult:
        reserve_call()
        result = self.inner.generate_json(**kwargs)
        add_cost(cost_usd(result.model, result.input_tokens, result.output_tokens) or Decimal(0))
        return result
