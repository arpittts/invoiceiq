"""Record-and-replay providers: CI scores real model replies without calling the model."""

import json

import pytest
from pydantic import BaseModel

from app.llm.provider import FakeProvider
from app.llm.replay import RecordingProvider, ReplayMismatch, ReplayProvider
from app.services.pdf import PageImage


class Answer(BaseModel):
    answer: str


PAGE = PageImage(b"png bytes", "image/png", 1414, 2000)


def call(provider, instruction="Total?", images=(PAGE,), system="You read invoices."):
    return provider.generate_json(
        model="m",
        system=system,
        images=list(images),
        instruction=instruction,
        output_type=Answer,
        effort="low",
    )


def recorded(*replies):
    recorder = RecordingProvider(FakeProvider(list(replies)))
    for _ in replies:
        call(recorder)
    return json.loads(json.dumps(recorder.calls))  # as saved to disk


def test_replay_returns_the_recorded_replies_in_order():
    calls = recorded('{"answer": "one"}', '{"answer": "two"}')
    replay = ReplayProvider(calls)
    assert call(replay).text == '{"answer": "one"}'
    second = call(replay)
    assert second.text == '{"answer": "two"}' and second.input_tokens == 1000
    assert replay.mismatches == []


def test_image_bytes_may_differ_but_not_their_size():
    calls = recorded('{"answer": "x"}')
    other_bytes = PageImage(b"rendered elsewhere", "image/png", 1414, 2000)
    assert call(ReplayProvider(calls), images=[other_bytes]).text == '{"answer": "x"}'
    other_size = PageImage(b"png bytes", "image/png", 1000, 2000)
    replay = ReplayProvider(calls)
    with pytest.raises(ReplayMismatch, match="images"):
        call(replay, images=[other_size])
    assert replay.mismatches


@pytest.mark.parametrize("change", [{"instruction": "Changed?"}, {"system": "New prompt."}])
def test_a_changed_prompt_fails_loudly(change):
    replay = ReplayProvider(recorded('{"answer": "x"}'))
    with pytest.raises(ReplayMismatch, match=next(iter(change))):
        call(replay, **change)


def test_asking_more_than_was_recorded_fails():
    replay = ReplayProvider(recorded('{"answer": "x"}'))
    call(replay)
    with pytest.raises(ReplayMismatch, match="never recorded"):
        call(replay)
    assert len(replay.mismatches) == 1


def test_recorded_failures_are_replayed_as_failures():
    class Broken:
        def generate_json(self, **kwargs):
            raise TimeoutError("quota exhausted")

    recorder = RecordingProvider(Broken())
    with pytest.raises(TimeoutError):
        call(recorder)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        call(ReplayProvider(recorder.calls))


def test_resuming_reuses_matching_calls_then_asks_the_real_model():
    earlier = recorded('{"answer": "old read"}', '{"answer": "old codes"}')
    resumed = RecordingProvider(FakeProvider(['{"answer": "new codes"}']), reuse=earlier)
    assert call(resumed).text == '{"answer": "old read"}'  # same request: reused
    assert call(resumed, instruction="Other candidates").text == '{"answer": "new codes"}'
    assert resumed.diverged and len(resumed.calls) == 2
