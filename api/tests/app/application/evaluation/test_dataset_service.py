import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.evaluation.dataset_service import DatasetService
from app.application.services.execution_content_service import ContentPage
from app.domain.models.scope import OwnerScope


@pytest.mark.asyncio
async def test_from_run_uses_complete_admitted_input_prefix_and_unconfirmed_output():
    run = uuid4()
    envelope = json.dumps(
        {
            "context": {
                "message": "actual",
                "conversation": [
                    {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "answer"},
                ],
                "attachments": [],
                "resource_bindings": [],
            },
            "request": {"round": 2},
        }
    )

    class Content:
        async def read_step_content(self, scope, run_id, step_id, at, cursor=None, *, content_kind):
            assert run_id == run
            assert at == "fixed-at"
            if content_kind == "output":
                return ContentPage(availability="available", content="historical output", at=at)
            return ContentPage(
                availability="available",
                content=envelope[:30] if cursor is None else envelope[30:],
                truncated=cursor is None,
                next_cursor="next" if cursor is None else None,
                at=at,
            )

    class Events:
        async def list_events(self, *args, **kwargs):
            return SimpleNamespace(
                events=[SimpleNamespace(event_type="message", payload={"message": "actual"})],
                next_cursor=None,
            )

    class Views:
        async def get_step_cut(self, *args):
            return SimpleNamespace(at="fixed-at")

    service = DatasetService(None, None, None, content=Content(), views=Views(), events=Events())
    case = await service.capture_case_from_run(
        OwnerScope.personal("u"), run_id=run, step_id="step", at="selected", case_key="a"
    )
    assert case.input == "actual"
    assert [message.content for message in case.history] == ["old", "answer"]
    assert case.reference_candidate == "historical output"
    assert case.reference_answer is None
    assert not case.reference_confirmed
    assert not case.input_confirmed
    assert case.input_status == "admitted"
    assert case.source_request == {"round": 2}


@pytest.mark.asyncio
async def test_from_run_redaction_is_never_verbatim_and_missing_input_has_no_summary_fallback():
    from app.domain.evaluation.errors import DatasetUnavailable

    class Views:
        async def get_step_cut(self, *args):
            return SimpleNamespace(at="fixed")

    class Content:
        available = True

        async def read_step_content(self, *args, **kwargs):
            return ContentPage(
                availability="available" if self.available else "unavailable",
                content=json.dumps(
                    {"context": {"message": "[redacted]", "conversation": []}, "request": {}}
                ),
                redacted=True,
                at="fixed",
            )

    class Events:
        async def list_events(self, *args, **kwargs):
            return SimpleNamespace(
                events=[
                    SimpleNamespace(event_type="message", payload={"message": "private original"})
                ],
                next_cursor=None,
            )

    content = Content()
    service = DatasetService(None, None, None, content=content, views=Views(), events=Events())
    case = await service.capture_case_from_run(
        OwnerScope.personal("u"), run_id=uuid4(), step_id="s", at="fixed", case_key="a"
    )
    assert case.input_status == "sanitized"
    content.available = False
    with pytest.raises(DatasetUnavailable, match="input_unavailable"):
        await service.capture_case_from_run(
            OwnerScope.personal("u"), run_id=uuid4(), step_id="s", at="fixed", case_key="a"
        )
