"""Demo safety: the daily LLM cap and the rate limits (blueprint section 18)."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import get_settings
from app.llm.provider import FakeProvider
from app.ratelimit import PER_IP, PER_USER
from app.services import budget, ledger_qa
from app.services.budget import BudgetedProvider, DailyCapReached
from tests.conftest import register_and_login


class Answer:
    __name__ = "Answer"


def call(provider):
    return provider.generate_json(
        model="gemini-3.5-flash-lite",
        system="s",
        images=[],
        instruction="i",
        output_type=Answer,
        effort="low",
    )


# --- daily cap --------------------------------------------------------------------


def test_calls_are_counted_and_refused_at_the_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_llm_call_limit", 3)
    provider = BudgetedProvider(FakeProvider(["{}"] * 5))
    for _ in range(3):
        call(provider)
    with pytest.raises(DailyCapReached, match="midnight India time"):
        call(provider)
    assert len(provider.inner.calls) == 3  # the refused call never reached the model
    usage = budget.usage_today()
    assert usage.calls == 3 and usage.cap_reached


def test_spend_counts_against_the_dollar_budget(monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_budget_usd", 0.01)
    provider = BudgetedProvider(FakeProvider(["{}"] * 3, model="claude-opus-5-5"))
    call(provider)  # 1000 in + 500 out tokens on Opus 5.5 = $0.014
    assert budget.usage_today().cost_usd > 0
    with pytest.raises(DailyCapReached):
        call(provider)


def test_parallel_requests_cannot_overshoot_the_cap(monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_llm_call_limit", 5)

    def try_reserve(_):
        try:
            budget.reserve_call()
            return True
        except DailyCapReached:
            return False

    with ThreadPoolExecutor(8) as pool:
        granted = sum(pool.map(try_reserve, range(20)))
    assert granted == 5 and budget.usage_today().calls == 5


def test_upload_is_refused_with_a_clear_message_when_the_cap_is_hit(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(get_settings(), "daily_llm_call_limit", 0)
    response = client.post(
        "/api/v1/invoices",
        headers=auth_headers,
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")},
    )
    assert response.status_code == 503
    assert "daily AI limit" in response.json()["detail"]


def test_ledger_question_explains_the_cap(monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_llm_call_limit", 0)
    provider = BudgetedProvider(FakeProvider([json.dumps({"sql": "SELECT 1", "reason": ""})]))
    result = ledger_qa.ask(provider, "How much did I spend?", user_id=1)
    assert "daily AI limit" in result.error and provider.inner.calls == []


# --- rate limits ------------------------------------------------------------------


def test_login_is_limited_per_ip(client):
    limit, _ = PER_IP["login"]
    body = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(limit):
        assert client.post("/api/v1/auth/login", json=body).status_code == 401
    blocked = client.post("/api/v1/auth/login", json=body)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_ask_is_limited_per_user_not_shared_between_users(client, monkeypatch):
    from app.api import ask as ask_api

    monkeypatch.setattr(ask_api, "get_provider", lambda: None)
    monkeypatch.setitem(PER_USER, "ask", (2, 3600))
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    question = {"question": "How much?"}
    for _ in range(2):
        assert client.post("/api/v1/ask", headers=alice, json=question).status_code == 200
    assert client.post("/api/v1/ask", headers=alice, json=question).status_code == 429
    assert client.post("/api/v1/ask", headers=bob, json=question).status_code == 200


def test_rate_limits_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limits_enabled", False)
    body = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(PER_IP["login"][0] + 3):
        assert client.post("/api/v1/auth/login", json=body).status_code == 401
