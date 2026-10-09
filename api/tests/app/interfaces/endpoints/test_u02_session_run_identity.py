"""Wire-only Run identity for the existing authenticated session stream."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.models.scope import OwnerScope
from app.interfaces.endpoints.session_routes import ChatRequest, chat


@pytest.mark.asyncio
async def test_session_stream_adds_real_run_identity_without_mutating_persisted_payload():
    run_id = uuid4()
    payload = {"event_id": str(uuid4()), "role": "user", "message": "new turn", "persist": True}
    before = dict(payload)

    async def events(**kwargs):
        yield SimpleNamespace(run_id=run_id, cursor="opaque", event_type="message", payload=payload)

    response = await chat(
        "session",
        ChatRequest(message="new turn", request_id=str(uuid4())),
        ctx=SimpleNamespace(scope=OwnerScope.personal(str(uuid4()))),
        _write_guard=SimpleNamespace(),
        agent_service=SimpleNamespace(chat=events),
        session_service=SimpleNamespace(get_session=AsyncMock(return_value=object())),
    )
    wire = [event async for event in response.body_iterator]
    assert json.loads(wire[0].data)["run_id"] == str(run_id)
    assert payload == before
