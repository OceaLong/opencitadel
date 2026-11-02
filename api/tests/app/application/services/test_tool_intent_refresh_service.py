from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.execution.command_ingress import CommandIngress
from app.application.services.agent_service import AgentService
from app.domain.errors import ForbiddenError
from app.domain.models.scope import OwnerScope, Principal


def service():
    usage = SimpleNamespace(
        authorize_principal=AsyncMock(),
        invocation_authority=AsyncMock(),
        tool_intent_refresh_target=AsyncMock(),
        approval_subject=AsyncMock(return_value="a" * 64),
    )
    sink = SimpleNamespace(receive=AsyncMock())
    uow = SimpleNamespace(execution_usage=usage, execution_commands=sink, commit=AsyncMock())
    auths = []

    @asynccontextmanager
    async def factory(auth=None):
        auths.append(auth)
        yield uow

    projection = SimpleNamespace(run_id_for_pending_approval=AsyncMock(return_value=uuid4()))
    agent = AgentService(
        uow_factory=factory,
        admission_service=None,
        command_ingress=CommandIngress(writer=sink),
        public_projection=None,
        run_projection=projection,
    )
    return agent, uow, auths


@pytest.mark.asyncio
async def test_refresh_uses_same_transaction_and_preserves_signed_run_requester():
    agent, uow, auths = service()
    scope, principal = OwnerScope.personal("owner"), Principal(user_id="owner")
    args = {
        "run_id": uuid4(),
        "activity_id": uuid4(),
        "owner_scope": scope,
        "principal": principal,
        "expected_subject_digest": "a" * 64,
        "reason": "Reviewed",
        "idempotency_key": "refresh-1",
    }
    first = await agent.refresh_tool_intent(**args)
    second = await agent.refresh_tool_intent(**args)
    assert first == second
    uow.execution_usage.invocation_authority.assert_awaited_with(scope, args["run_id"], lock=True)
    uow.execution_usage.authorize_principal.assert_awaited_with(scope, principal, lock=True)
    sent = uow.execution_commands.receive.call_args.args[0]
    assert sent.command_id == first["command_id"]
    assert sent.command_type == "RefreshToolIntent"
    assert sent.payload["expected_subject_digest"] == args["expected_subject_digest"]
    assert sent.payload["actor_user_id"] == "owner"
    assert "requester" not in sent.payload
    assert auths[0] is not None
    assert uow.commit.await_count == 2


@pytest.mark.asyncio
async def test_revoked_original_admission_cannot_be_replaced_by_current_token():
    agent, uow, _ = service()
    uow.execution_usage.invocation_authority.side_effect = PermissionError("revoked")
    with pytest.raises(ForbiddenError):
        await agent.refresh_tool_intent(
            run_id=uuid4(),
            activity_id=uuid4(),
            owner_scope=OwnerScope.personal("owner"),
            principal=Principal(user_id="owner", token_version=2),
            expected_subject_digest="a" * 64,
            reason="Reviewed",
            idempotency_key="refresh-1",
        )
    uow.execution_commands.receive.assert_not_called()
    uow.commit.assert_not_called()


@pytest.mark.asyncio
async def test_approval_command_carries_formally_recorded_subject():
    agent, uow, _ = service()
    await agent.decide_approval(
        approval_id=uuid4(),
        owner_scope=OwnerScope.personal("owner"),
        decision="approved",
        actor_user_id="owner",
    )
    sent = uow.execution_commands.receive.call_args.args[0]
    assert sent.command_schema_version == 2
    assert sent.payload["expected_subject_digest"] == "a" * 64
