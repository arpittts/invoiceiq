from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.config import get_settings
from app.llm import provider as provider_module
from app.llm.provider import AnthropicProvider, GeminiProvider
from app.schemas.extraction import ExtractionOutput
from app.services.extraction import get_provider
from app.services.pdf import PageImage

PAGES = [
    PageImage(b"\x89PNG-1", "image/png", 10, 10),
    PageImage(b"\xff\xd8-2", "image/jpeg", 10, 10),
]


def response(text='{"ok": true}', finish="STOP"):
    """A real SDK response object, as Gemini would return it."""
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]),
                finish_reason=genai_types.FinishReason[finish],
            )
        ],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1200, candidates_token_count=300, thoughts_token_count=50
        ),
        model_version="models/gemini-3.5-flash-lite",
        response_id="resp_1",
    )


class FakeModels:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def generate_content(self, **request):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def gemini_with(results, monkeypatch) -> tuple[GeminiProvider, FakeModels]:
    provider = GeminiProvider(api_key="test-key")
    fake = FakeModels(results)
    provider.client = SimpleNamespace(models=fake)
    monkeypatch.setattr(provider_module.time, "sleep", lambda seconds: None)  # no real waiting
    return provider, fake


def call(provider):
    return provider.generate_json(
        model="gemini-3.5-flash-lite", system="SYSTEM RULES", images=PAGES,
        instruction="Extract it.", output_type=ExtractionOutput, effort="medium",
    )  # fmt: skip


def test_gemini_request_shape(monkeypatch):
    provider, fake = gemini_with([response()], monkeypatch)
    call(provider)
    [request] = fake.requests
    config = request["config"]

    assert config.system_instruction == "SYSTEM RULES"  # rules separate from the document
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == ExtractionOutput.model_json_schema()
    assert not config.tools  # the extraction step is never given tools
    images = [part for part in request["contents"] if isinstance(part, genai_types.Part)]
    assert [image.inline_data.mime_type for image in images] == ["image/png", "image/jpeg"]
    assert request["contents"][-1] == "Extract it."


def test_gemini_client_has_a_timeout():
    provider = GeminiProvider(api_key="test-key")
    assert provider.client._api_client._http_options.timeout == 120_000  # milliseconds


def test_gemini_response_mapping(monkeypatch):
    provider, _ = gemini_with([response()], monkeypatch)
    result = call(provider)
    assert result.text == '{"ok": true}'
    assert result.model == "gemini-3.5-flash-lite"  # "models/" prefix removed for price lookup
    assert (result.input_tokens, result.output_tokens) == (1200, 350)  # thinking counts as output
    assert result.stop_reason == "end_turn"


@pytest.mark.parametrize(
    ("finish", "expected"), [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal")]
)
def test_gemini_finish_reasons(monkeypatch, finish, expected):
    provider, _ = gemini_with([response(finish=finish)], monkeypatch)
    assert call(provider).stop_reason == expected


def quota_error(message):
    return genai_errors.ClientError(429, {"error": {"code": 429, "message": message}})


def test_gemini_retries_short_rate_limits(monkeypatch):
    limited = quota_error("Too many requests. Please retry in 7s.")
    provider, fake = gemini_with([limited, limited, response()], monkeypatch)
    assert call(provider).text == '{"ok": true}'
    assert len(fake.requests) == 3


def test_gemini_fails_fast_when_the_daily_quota_is_used_up(monkeypatch):
    daily = quota_error("Quota exceeded, limit: 20. Please retry in 9h58m31s.")
    provider, fake = gemini_with([daily, response()], monkeypatch)
    with pytest.raises(genai_errors.ClientError):
        call(provider)
    assert len(fake.requests) == 1  # no pointless waiting


def test_gemini_does_not_retry_bad_requests(monkeypatch):
    bad_request = genai_errors.ClientError(400, {"error": {"message": "bad schema"}})
    provider, fake = gemini_with([bad_request, response()], monkeypatch)
    with pytest.raises(genai_errors.ClientError):
        call(provider)
    assert len(fake.requests) == 1


def test_gemini_retries_timeouts_then_gives_up(monkeypatch):
    timeout = httpx.ReadTimeout("slow")
    provider, _ = gemini_with([timeout, response()], monkeypatch)
    assert call(provider).text == '{"ok": true}'

    provider, fake = gemini_with([timeout] * 4, monkeypatch)
    with pytest.raises(httpx.ReadTimeout):
        call(provider)
    assert len(fake.requests) == 4  # first try + 3 retries


def test_get_provider_follows_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "")
    assert get_provider() is None
    monkeypatch.setattr(settings, "llm_api_key", "some-key")
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    # Real providers always come wrapped in the daily cap.
    assert isinstance(get_provider().inner, GeminiProvider)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    assert isinstance(get_provider().inner, AnthropicProvider)
