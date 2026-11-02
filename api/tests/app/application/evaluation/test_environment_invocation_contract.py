"""Isolated real calls share invocation/effect fencing without ordinary discovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.application.evaluation.environment_runtime import EnvironmentRuntime
from app.application.execution.activities.model_call import _normalize_tool_calls
from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.application.execution.agent_tool_catalog import AgentToolCatalog
from app.application.execution.invocation_contracts import freeze_calls
from app.domain.execution.tool_contract import RecordedContract, ToolApprovalSubject
from app.domain.models.scope import OwnerScope, Principal
from app.domain.services.tools.capability_policy import EXTERNAL_WRITE
from tests.app.application.execution.test_invocation_contracts import _setup, _Write


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_isolated_write_exact_binding_preflights_in_effect_transaction(revoked):
    context, uow, _, _, objects, request = await _setup(_Write())
    descriptor = _Write().get_tool_descriptors()[0]
    contract = RecordedContract(
        name=descriptor.name,
        pack="mcp",
        schema_body=descriptor.schema,
        policy=EXTERNAL_WRITE,
        connector_id="fixture",
        binding_revision="fixture-v1",
        authority_revision="fixture-v1",
    )
    target = SimpleNamespace(id=UUID(int=40), contracts=(contract,))
    lease = SimpleNamespace(id=UUID(int=41), environment_version=UUID(int=42), generation=1)
    principal = Principal(user_id="user-1", token_version=1, global_role="user", team_roles={})
    scope = OwnerScope.personal("user-1")
    admitted = {
        "config_fingerprint": "fixed",
        "contracts": [
            {
                "name": contract.name,
                "pack": contract.pack,
                "schema": contract.schema_body,
                "policy": contract.policy.model_dump(mode="json"),
            }
        ],
    }
    executor = SimpleNamespace(invoke=AsyncMock(return_value={"success": True, "data": "done"}))
    executor.bind = lambda adapter, lease: executor
    adapter = SimpleNamespace(revision="fixture-v1", check_runtime=AsyncMock())
    runtime = EnvironmentRuntime(
        lambda: None, SimpleNamespace(executors={target.id: executor}), None
    )
    accesses = []

    async def access(context, *, tool=None, uow=None, locked=False, approval_subject_digest=None):
        accesses.append((uow, locked))
        if tool is not None:
            assert (
                approval_subject_digest
                == ToolApprovalSubject.model_validate(calls[0]["subject"]).digest
            )
        effective_principal = (
            principal.model_copy(update={"token_version": 2}) if revoked and locked else principal
        )
        return adapter, lease, (target,), (), admitted, scope, effective_principal

    runtime.access = access
    runtime.active = AsyncMock(return_value=True)
    snapshot = await runtime.definitions(context)
    calls = _normalize_tool_calls(
        [{"id": "call-1", "function": {"name": contract.name, "arguments": {"key": "original"}}}],
        snapshot.definitions,
    )
    await freeze_calls(
        objects, request.model_copy(update={"activity_id": UUID(int=101)}), snapshot, calls
    )
    request = request.model_copy(
        update={
            "input_payload": {
                "tool_call": {key: calls[0][key] for key in ("call_id", "name", "arguments")},
                "subject": calls[0]["subject"],
            }
        }
    )
    catalog = object.__new__(AgentToolCatalog)
    catalog._isolated = runtime
    catalog._replay = None
    catalog._build = AsyncMock(
        side_effect=AssertionError("ordinary catalog must remain unreachable")
    )
    specs = []

    async def invoke(spec, context, send, *, preflight):
        await preflight(uow)
        specs.append(spec)
        return SimpleNamespace(state="applied", result=await send())

    outcome = await ToolCallActivityHandler(
        objects=objects,
        tools=catalog,
        effects=SimpleNamespace(invoke=invoke, recover=AsyncMock(return_value=None)),
    ).execute(request, context)
    assert accesses[-1] == (uow, True)
    if revoked:
        assert outcome.status == "failed"
        assert outcome.failure_code == "TOOL_AUTHORIZATION_CHANGED"
        executor.invoke.assert_not_called()
        assert specs == []
    else:
        assert outcome.status == "succeeded"
        executor.invoke.assert_awaited_once()
        assert specs[0].capability == "manual_only"
    catalog._build.assert_not_called()


@pytest.mark.asyncio
async def test_approval_authority_reads_formal_exact_facts_without_projection():
    from app.infrastructure.repositories.db_evaluation_recording_repository import (
        DBEvaluationRecordingRepository,
    )

    # The projection is intentionally absent. Only formal approved facts with
    # the final subject are supplied by the repository's transaction boundary.
    session = SimpleNamespace(scalar=AsyncMock(return_value=1))
    repository = DBEvaluationRecordingRepository(session)
    assert await repository.approved(
        OwnerScope.personal("user-1"), UUID(int=1), UUID(int=2), subject_digest="a" * 64
    )
    statement, parameters = session.scalar.await_args.args
    assert "execution_approval_projection" not in str(statement)
    assert "execution_events" in str(statement)
    assert "expected_subject_digest" in str(statement)
    assert parameters["subject"] == "a" * 64
    assert parameters["run"] == str(UUID(int=1))
