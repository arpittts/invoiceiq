"""LLM tracing with Langfuse.

Every model call becomes a Langfuse "generation" (model, tokens, cost, latency),
grouped under one trace per invoice. Without Langfuse keys, tracing is a no-op,
so local development and tests don't need an account.

Privacy (blueprint section 19): traces carry ids, counts and confidences only.
Invoice images and extracted values (GSTINs, amounts) are never sent.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from app.config import get_settings


class _NoOpObservation:
    trace_id: str | None = None

    def update(self, **kwargs: Any) -> None:
        pass


@lru_cache
def get_langfuse():
    """The Langfuse client, or None when tracing is not configured."""
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    from langfuse import Langfuse  # imported lazily: only needed when tracing is on

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_host,
    )


@contextmanager
def trace_span(name: str, metadata: dict) -> Iterator[Any]:
    """A parent span, e.g. one per invoice. Generations started inside it nest under it."""
    langfuse = get_langfuse()
    if langfuse is None:
        yield _NoOpObservation()
        return
    with langfuse.start_as_current_observation(
        name=name, as_type="span", metadata=metadata
    ) as span:
        yield span


@contextmanager
def trace_generation(name: str, model: str, metadata: dict) -> Iterator[Any]:
    """One LLM call. Call .update(output=..., usage_details=..., cost_details=...) on the result."""
    langfuse = get_langfuse()
    if langfuse is None:
        yield _NoOpObservation()
        return
    with langfuse.start_as_current_observation(
        name=name, as_type="generation", model=model, metadata=metadata
    ) as generation:
        yield generation


def flush_traces() -> None:
    """Send buffered traces now. Needed at the end of short-lived scripts."""
    langfuse = get_langfuse()
    if langfuse is not None:
        langfuse.flush()
