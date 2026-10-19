"""Record real model replies once, replay them later (blueprint section 12).

CI has no API key and must not depend on a rate-limited service, but it should
still catch regressions in everything around the model: parsing, validation,
the agent's routing, merging re-reads, the SQL guard, scoring. So:

- RecordingProvider wraps a real provider and keeps every request's fingerprint
  next to the reply it got.
- ReplayProvider serves those replies back in the same order and checks each
  request's fingerprint first. If our prompts, schema, model or page rendering
  change, the fingerprint no longer matches and it fails loudly (re-record with
  `make eval-record`), instead of silently scoring stale replies.

Fingerprints hash the prompt text but keep only the size of each page image:
rendering can differ by a few bytes between machines, but the size can't.
"""

import hashlib
import time
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel

from app.llm.provider import LLMProvider, LLMResult
from app.services.pdf import PageImage


class ReplayMismatch(Exception):
    """The code asked the model something the recording doesn't have."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fingerprint(
    *,
    model: str,
    system: str,
    images: list[PageImage],
    instruction: str,
    output_type: type[BaseModel],
) -> dict:
    return {
        "model": model,
        "system": _sha(system),
        "instruction": _sha(instruction),
        "schema": output_type.__name__,
        "images": [[image.width, image.height] for image in images],
    }


@dataclass
class RecordingProvider:
    """Passes calls to a real provider and keeps (fingerprint, reply) pairs.

    `reuse` (for resuming an interrupted recording): earlier calls are served from
    it while the requests still match; from the first difference on, the real
    model answers. (HSN candidates can differ between runs, because the vector
    index is approximate, so a case may reuse its read but re-ask for codes.)
    """

    inner: LLMProvider
    reuse: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    diverged: bool = False

    def generate_json(self, **kwargs) -> LLMResult:
        request = fingerprint(**{k: kwargs[k] for k in _FINGERPRINT_ARGS})
        position = len(self.calls)
        if not self.diverged and position < len(self.reuse):
            earlier = self.reuse[position]
            if earlier["request"] == request and "error" not in earlier:
                self.calls.append(earlier)
                return LLMResult(**earlier["reply"])
        self.diverged = True
        started = time.monotonic()
        try:
            result = self.inner.generate_json(**kwargs)
        except Exception as error:
            # Failures are part of the run too (e.g. a quota error): replay them as failures.
            self.calls.append(
                {
                    "request": request,
                    "error": f"{type(error).__name__}: {error}"[:500],
                    "latency_ms": round((time.monotonic() - started) * 1000),
                }
            )
            raise
        self.calls.append({"request": request, "reply": asdict(result)})
        return result


@dataclass
class ReplayProvider:
    """Serves recorded replies in order. No network, no key, no cost."""

    calls: list[dict]
    position: int = 0
    # Callers like the extraction service catch provider errors and carry on, so
    # mismatches are also kept here for the harness to check after each case.
    mismatches: list[str] = field(default_factory=list)

    def _mismatch(self, message: str) -> ReplayMismatch:
        self.mismatches.append(message)
        return ReplayMismatch(message)

    def generate_json(self, **kwargs) -> LLMResult:
        request = fingerprint(**{k: kwargs[k] for k in _FINGERPRINT_ARGS})
        if self.position >= len(self.calls):
            raise self._mismatch(
                f"call {self.position + 1} ({request['schema']}) was never recorded"
            )
        recorded = self.calls[self.position]
        self.position += 1
        if recorded["request"] != request:
            changed = sorted(k for k in request if recorded["request"].get(k) != request[k])
            raise self._mismatch(
                f"call {self.position} ({request['schema']}) differs from the recording "
                f"in: {', '.join(changed)}"
            )
        if "error" in recorded:
            raise RuntimeError(f"(replayed) {recorded['error']}")
        return LLMResult(**recorded["reply"])


_FINGERPRINT_ARGS = ("model", "system", "images", "instruction", "output_type")
