import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.evaluation.recording_source import RecordingSource
from app.domain.evaluation.errors import ReplayMismatch


@pytest.mark.asyncio
async def test_full_content_continuation_beyond_public_summary():
    identity = str(uuid4())
    body = json.dumps({"data": "x" * 90000})
    calls = []

    class Content:
        async def read_step_content(self, scope, run_id, step_id, at, cursor, *, content_kind):
            calls.append(cursor)
            return SimpleNamespace(
                availability="available",
                content=body[:65536] if cursor is None else body[65536:],
                at=at,
                content_id=identity,
                redacted=False,
                truncated=cursor is None,
                next_cursor="next" if cursor is None else None,
            )

    source = RecordingSource(Content(), None)
    value, redacted, content_id = await source.complete("scope", uuid4(), "step", "fixed", "output")
    assert value == {"data": "x" * 90000}
    assert calls == [None, "next"]
    assert not redacted
    assert str(content_id) == identity


@pytest.mark.asyncio
async def test_steps_use_real_page_items_and_one_fixed_cut():
    class Views:
        async def get_view(self, scope, run_id, at=None):
            assert at is None
            return SimpleNamespace(run=SimpleNamespace(status="succeeded"), at="fixed")

        async def list_steps(self, *args, at, cursor, limit):
            assert at == "fixed"
            return SimpleNamespace(
                items=["one"] if cursor is None else ["two"],
                next_cursor="second" if cursor is None else None,
            )

    assert await RecordingSource(None, Views()).steps("scope", uuid4()) == ("fixed", ["one", "two"])


@pytest.mark.asyncio
async def test_revocation_on_continuation_cannot_return_partial_recording():
    class Content:
        async def read_step_content(self, *args, content_kind):
            if args[-1] is not None:
                raise PermissionError("revoked")
            return SimpleNamespace(
                availability="available",
                content="partial",
                at="fixed",
                content_id=str(uuid4()),
                redacted=False,
                truncated=True,
                next_cursor="next",
            )

    with pytest.raises(PermissionError):
        await RecordingSource(Content(), None).complete("scope", uuid4(), "step", "fixed", "output")


@pytest.mark.asyncio
async def test_generation_rejects_incomplete_activity_before_any_result_read():
    from app.application.evaluation.recording_worker import RecordingWorker

    class Source:
        async def steps(self, *args):
            return "fixed", [SimpleNamespace(activity_id=uuid4(), status="failed")]

        async def complete(self, *args):
            pytest.fail("incomplete recording read result body")

    worker = RecordingWorker(SimpleNamespace(source=Source()), None)
    with pytest.raises(ReplayMismatch, match="source_activity_incomplete"):
        await worker._generate(
            "scope", "principal", {"source_run_id": uuid4(), "selection": []}, uuid4()
        )
