"""A thin layer between our code and the LLM vendor (blueprint section 5).

The extraction service only knows the `LLMProvider` interface, so the vendor is
a setting (LLM_PROVIDER): Google Gemini (free tier, the default) or Anthropic
Claude. Tests use `FakeProvider` (no network, no cost).
"""

import base64
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel

from app.services.pdf import PageImage

# Models that accept server-side refusal fallbacks ("default" form). If a safety
# classifier declines, the API retries on a suitable model inside the same call.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class LLMResult:
    """One model call. `text` is the raw JSON string, validated by the caller."""

    text: str | None
    model: str  # the model that actually served the request (may be a fallback)
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    request_id: str | None = None


class LLMProvider(Protocol):
    def generate_json(
        self,
        *,
        model: str,
        system: str,
        images: list[PageImage],
        instruction: str,
        output_type: type[BaseModel],
        effort: str,
    ) -> LLMResult: ...


class AnthropicProvider:
    def __init__(self, api_key: str, timeout_seconds: float = 180.0):
        # The SDK retries 429/5xx/connection errors itself (max_retries).
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=2)

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        images: list[PageImage],
        instruction: str,
        output_type: type[BaseModel],
        effort: str,
    ) -> LLMResult:
        # The SDK turns the Pydantic model into the JSON schema format the API accepts.
        json_schema = anthropic.transform_schema(output_type)

        # Page images first, each labelled, then the instruction (images-then-text
        # works best for vision). Invoice content only ever appears here, in the
        # user turn as data; our instructions live in the system prompt.
        content: list[dict] = []
        for number, page in enumerate(images, start=1):
            content.append({"type": "text", "text": f"Page {number} of {len(images)}:"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": page.media_type,
                        "data": base64.standard_b64encode(page.data).decode(),
                    },
                }
            )
        content.append({"type": "text", "text": instruction})

        extra: dict = {}
        if model in _FALLBACK_MODELS:
            extra = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}

        started = time.perf_counter()
        response = self.client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            # Structured outputs: the reply is guaranteed to be JSON matching the schema.
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": json_schema},
            },
            **extra,
        )
        latency_ms = round((time.perf_counter() - started) * 1000)

        text = next((block.text for block in response.content if block.type == "text"), None)
        return LLMResult(
            text=text,
            model=response.model,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            request_id=response._request_id,
        )


class GeminiProvider:
    """Google Gemini (generate_content) with JSON-schema structured output.

    Free-tier note: Google may use free-tier inputs to improve its products, so
    only synthetic or demo invoices should be sent while on the free tier.
    """

    # Short rate limits and server hiccups: wait and try again.
    RETRY_STATUS = {429, 500, 502, 503, 504}
    RETRY_DELAYS_SECONDS = (5, 15, 45)
    # Without a timeout a stuck request can hang forever (seen in testing).
    TIMEOUT_SECONDS = 120

    # Gemini finish reasons that mean "the model declined": not worth retrying.
    _DECLINED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION",
                 "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}  # fmt: skip

    def __init__(self, api_key: str):
        self.client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=self.TIMEOUT_SECONDS * 1000),  # in ms
        )

    def generate_json(
        self,
        *,
        model: str,
        system: str,
        images: list[PageImage],
        instruction: str,
        output_type: type[BaseModel],
        effort: str,  # Claude-specific; Gemini Flash has no equivalent setting here
    ) -> LLMResult:
        # Page images first, each labelled, then the instruction. Invoice content
        # only ever appears here as data; our rules are the system instruction.
        contents: list = []
        for number, page in enumerate(images, start=1):
            contents.append(f"Page {number} of {len(images)}:")
            contents.append(genai_types.Part.from_bytes(data=page.data, mime_type=page.media_type))
        contents.append(instruction)

        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            # Structured output: the reply is JSON matching this schema.
            response_mime_type="application/json",
            response_json_schema=output_type.model_json_schema(),
        )
        started = time.perf_counter()
        response = self._generate_with_retries(model=model, contents=contents, config=config)
        latency_ms = round((time.perf_counter() - started) * 1000)

        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason.name if candidate and candidate.finish_reason else None
        if candidate is None or finish in self._DECLINED:
            stop_reason = "refusal"  # blocked prompt or a safety stop
        elif finish == "MAX_TOKENS":
            stop_reason = "max_tokens"
        else:
            stop_reason = "end_turn"

        usage = response.usage_metadata
        return LLMResult(
            text=response.text if candidate is not None else None,
            model=(response.model_version or model).removeprefix("models/"),
            stop_reason=stop_reason,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            # Thinking tokens are billed as output on paid tiers, so count them too.
            output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
            if usage
            else 0,
            latency_ms=latency_ms,
            request_id=response.response_id,
        )

    @staticmethod
    def _is_daily_quota(error: genai_errors.APIError) -> bool:
        """A daily free-tier quota resets hours later, so waiting seconds won't help."""
        message = str(error)
        return error.code == 429 and ("PerDay" in message or re.search(r"retry in \d+h", message))

    def _generate_with_retries(self, **request):
        for delay in (*self.RETRY_DELAYS_SECONDS, None):
            try:
                return self.client.models.generate_content(**request)
            except genai_errors.APIError as error:
                if (
                    delay is None
                    or error.code not in self.RETRY_STATUS
                    or self._is_daily_quota(error)
                ):
                    raise
            except (httpx.TimeoutException, httpx.TransportError):
                # Timed out or the connection dropped: worth another try.
                if delay is None:
                    raise
            time.sleep(delay)


@dataclass
class FakeProvider:
    """Returns canned replies in order. Records every call for assertions."""

    replies: list[str | None]
    model: str = "fake-model"
    calls: list[dict] = field(default_factory=list)

    def generate_json(self, **kwargs) -> LLMResult:
        self.calls.append(kwargs)
        text = self.replies[len(self.calls) - 1]
        return LLMResult(
            text=text,
            model=self.model,
            stop_reason="end_turn" if text is not None else "refusal",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=5,
        )
