"""Exact selected intents, recovery safety and transport uncertainty."""

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.application.evaluation.configuration_metadata import contract_fingerprints
from app.application.execution.activities.model_call import _normalize_tool_calls
from app.application.execution.activities.tool_call import ToolCallActivityHandler
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.agent_tool_catalog import AgentToolCatalog, _BuiltCatalog
from app.application.execution.invocation_contracts import finalize_subject, freeze_calls
from app.domain.evaluation.configuration import digest
from app.domain.execution.activity import ActivityContext, ActivityRequest
from app.domain.execution.tool_contract import (
    RecordedContract,
    ToolApprovalSubject,
    execution_contract,
)
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.capability_policy import EXTERNAL_WRITE, READ_SAFE
from tests.app.execution_test_support import run_execution_context_for


class _Storage:
    def __init__(self):
        self.data = {}

    async def put_bytes(self, key, value):
        self.data[key] = value

    async def get_bytes(self, key):
        return self.data[key]


class _Read(BaseTool):
    name = "read"

    def __init__(self):
        super().__init__()
        self.sends = []

    @tool(
        name="read_value",
        description="Read",
        parameters={"key": {"type": "string"}},
        required=["key"],
        policy=READ_SAFE,
    )
    async def read_value(self, key):
        self.sends.append(key)
        return ToolResult(success=True, data=key)


class _Write(BaseTool):
    name = "write"

    def __init__(self):
        super().__init__()
        self.sends = []

    @tool(
        name="write_value",
        description="Write",
        parameters={"key": {"type": "string"}},
        required=["key"],
        policy=EXTERNAL_WRITE,
    )
    async def write_value(self, key):
        self.sends.append(key)
        return ToolResult(success=True, data=key)


async def _setup(pack):
    context = ActivityContext(
        worker_id="worker",
        claim_generation=1,
        idempotency_key="claim",
        owner_user_id="user-1",
        team_id=None,
        run=run_execution_context_for("agent"),
    )
    authority = {
        "scope": context.run.owner_scope.model_dump(mode="json"),
        "authorization_epoch": "a" * 64,
    }
    uow = SimpleNamespace(
        execution_usage=SimpleNamespace(
            invocation_authority=AsyncMock(return_value=authority), authorize_tool=AsyncMock()
        ),
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def factory():
        yield uow

    catalog = object.__new__(AgentToolCatalog)
    catalog._uow_factory = factory
    catalog._contract_capture = None
    catalog._replay = None
    catalog._isolated = None
    built = _BuiltCatalog(packs=[pack], fingerprint="old")
    catalog._build = AsyncMock(return_value=built)
    snapshot = await catalog.definitions({}, context)
    objects = ActivityObjectStore(_Storage())
    ref, body_digest = await objects.put_input(
        context.run.run_id, {"mode": "agent", "session_id": "session"}
    )
    model_request = ActivityRequest(
        activity_id=UUID(int=101),
        activity_type="model.call",
        aggregate_type="run",
        aggregate_id=str(context.run.run_id),
        generation=0,
        timeout_at=datetime.now(UTC) + timedelta(minutes=1),
        input_ref=ref,
        input_digest=body_digest,
    )
    calls = _normalize_tool_calls(
        [
            {
                "id": "call-1",
                "function": {"name": snapshot.tool_names[0], "arguments": {"key": "original"}},
            }
        ],
        snapshot.definitions,
    )
    await freeze_calls(objects, model_request, snapshot, calls)
    call = calls[0]
    request = model_request.model_copy(
        update={
            "activity_id": UUID(int=102),
            "activity_type": "tool.call",
            "input_payload": {
                "tool_call": {key: call[key] for key in ("call_id", "name", "arguments")},
                "subject": call["subject"],
            },
        }
    )
    return context, uow, catalog, built, objects, request


@pytest.mark.asyncio
async def test_native_safe_recovery_and_unrelated_catalog_drift():
    pack = _Read()
    context, _, catalog, built, objects, request = await _setup(pack)
    built.packs.append(_Write())
    built.fingerprint = "different-whole-catalog"
    handler = ToolCallActivityHandler(objects=objects, tools=catalog)
    assert await handler.recovery_safe(request, context.run)
    outcome = await handler.execute(request, context)
    assert outcome.status == "succeeded"
    assert pack.sends == ["original"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    ["schema", "arguments", "policy", "target", "binding", "revocation", "removal"],
)
async def test_selected_contract_or_authority_drift_sends_nothing_and_never_becomes_model_tool_error(
    drift,
):
    pack = _Read()
    context, uow, catalog, built, objects, request = await _setup(pack)
    if drift == "schema":
        original = pack.get_tool_descriptors()[0]
        pack.get_tool_descriptors = lambda: [
            replace(original, schema={**original.schema, "description": "changed"})
        ]
    elif drift == "arguments":
        request = request.model_copy(
            update={
                "input_payload": {
                    **request.input_payload,
                    "tool_call": {
                        **request.input_payload["tool_call"],
                        "arguments": {"key": "changed"},
                    },
                }
            }
        )
    elif drift == "policy":
        original = pack.get_tool_descriptors()[0]
        pack.get_tool_descriptors = lambda: [replace(original, policy=EXTERNAL_WRITE)]
    elif drift == "target":
        subject = ToolApprovalSubject.model_validate(request.input_payload["subject"])
        request = request.model_copy(
            update={
                "input_payload": {
                    **request.input_payload,
                    "subject": subject.model_copy(update={"target_digest": "f" * 64}).model_dump(
                        mode="json"
                    ),
                }
            }
        )
    elif drift == "binding":
        subject = ToolApprovalSubject.model_validate(request.input_payload["subject"])
        frozen = RecordedContract.model_validate(
            await objects.load_input(
                key=subject.contract_ref, expected_digest=subject.contract_object_digest
            )
        )
        changed = frozen.model_copy(
            update={
                "connector_bindings": {"selected-connector": "b" * 64},
                "binding_revision": "b" * 64,
                "authority_revision": "b" * 64,
            }
        )
        catalog._contract_capture = SimpleNamespace(describe=AsyncMock(return_value=(changed,)))
    elif drift == "revocation":
        uow.execution_usage.invocation_authority.side_effect = PermissionError("revoked")
    else:
        built.packs.clear()
    outcome = await ToolCallActivityHandler(objects=objects, tools=catalog).execute(
        request, context
    )
    assert outcome.status == "failed"
    assert outcome.failure_code in {"TOOL_CONTRACT_CHANGED", "TOOL_AUTHORIZATION_CHANGED"}
    assert outcome.result_ref is None
    assert outcome.public_data["status"] == "not_sent"
    assert pack.sends == []


@pytest.mark.asyncio
async def test_write_uses_one_effect_identity_and_unknown_is_not_tool_failure():
    pack = _Write()
    context, uow, catalog, _, objects, request = await _setup(pack)
    observed = []

    async def invoke(spec, passed_context, send, *, preflight):
        await preflight(uow)
        observed.append(spec)
        await send()
        return SimpleNamespace(state="unknown", result=None)

    handler = ToolCallActivityHandler(
        objects=objects,
        tools=catalog,
        effects=SimpleNamespace(invoke=invoke, recover=AsyncMock(return_value=None)),
    )
    assert await handler.recovery_safe(request, context.run)
    result = await handler.execute(request, context)
    assert result.status == "unknown"
    assert result.result_ref is None
    assert observed[0].operation_id == observed[0].invocation_id
    assert observed[0].provider_locator["tool_call_id"] == "call-1"
    assert observed[0].capability == "manual_only"
    assert pack.sends == ["original"]


@pytest.mark.asyncio
async def test_feedback_changes_subject_and_invocation_before_final_preview():
    _, _, _, _, _, request = await _setup(_Write())
    original = ToolApprovalSubject.model_validate(request.input_payload["subject"])
    final = finalize_subject(original, {"key": "original", "choice": "production"})
    assert final.invocation_id != original.invocation_id
    assert final.digest != original.digest
    assert final.contract_digest == original.contract_digest
    assert final.arguments_digest == digest({"key": "original", "choice": "production"})


def test_v1_wire_and_legacy_fingerprint_unchanged_full_contract_versioned():
    descriptor = _Read().get_tool_descriptors()[0]
    body = {
        "name": descriptor.name,
        "pack": "read",
        "schema_body": descriptor.schema,
        "policy": descriptor.policy.model_dump(mode="json"),
        "connector_id": None,
        "source_name": None,
        "connector_bindings": {},
        "binding_revision": "x",
        "authority_revision": "x",
    }
    v1 = RecordedContract.model_validate(body)
    assert v1.model_dump(mode="json") == body
    assert v1.digest == digest(body)
    v2 = execution_contract(v1)
    assert v2.digest != v1.digest
    assert v2.recovery_safe
    item = {
        "name": descriptor.name,
        "pack": "read",
        "schema": descriptor.schema,
        "policy": body["policy"],
    }
    legacy, full = contract_fingerprints([item], mode="agent", skill=None)
    expected = hashlib.sha256(
        json.dumps(
            {
                "mode": "agent",
                "skill": None,
                "tools": [{key: item[key] for key in ("name", "pack", "policy")}],
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert legacy == expected
    changed = {**item, "schema": {**item["schema"], "description": "changed"}}
    new_legacy, new_full = contract_fingerprints([changed], mode="agent", skill=None)
    assert new_legacy == legacy
    assert new_full != full


@pytest.mark.asyncio
@pytest.mark.parametrize("approval_kind", ["tool_effect", "clarification"])
async def test_feedback_precedes_exact_preview_and_aggregate_binds_the_approved_intent(
    approval_kind,
):
    from app.application.execution.decisions import next_command
    from app.application.execution.decisions.base import approval_identity
    from app.application.execution.run_context import run_execution_context
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate, decision_data_digest
    from tests.app.application.execution.test_clarification_flow import NOW, _clarifying_model_round

    _, _, _, _, _, request = await _setup(_Write())
    state, model_id, data, _ = _clarifying_model_round()
    original = ToolApprovalSubject.model_validate(request.input_payload["subject"])
    subject = finalize_subject(
        original.model_copy(update={"originating_activity_id": model_id, "tool_call_id": "ask-1"}),
        {"key": "original"},
    )
    data["tool_calls"] = [
        {
            "call_id": "ask-1",
            "name": "write_value",
            "arguments": {"key": "original"},
            "requires_approval": True,
            "risk_summary": "Write target",
            "approval_kind": approval_kind,
            "approval_feedback_param": "choice",
            "subject": subject.model_dump(mode="json"),
        }
    ]
    state = state.model_copy(
        update={
            "activity_results": tuple(
                (a, g, r, s, decision_data_digest(data) if a == model_id else d)
                for a, g, r, s, d in state.activity_results
            )
        }
    )
    initial = next_command(state, run_execution_context(state), outcomes={model_id: data}, now=NOW)
    assert initial.payload["subject"] is None
    selected = state.model_copy(
        update={
            "approval_decisions": (
                (approval_identity(state, "tool:0:0:ask-1"), "approved", "production"),
            )
        }
    )
    preview = next_command(
        selected, run_execution_context(selected), outcomes={model_id: data}, now=NOW
    )
    assert preview.command_type == "RequestApproval"
    assert preview.command_schema_version == 2
    assert preview.payload["approval_kind"] == "tool_effect"
    assert json.loads(preview.payload["public_preview"]["arguments_json"])["choice"] == "production"
    assert preview.payload["subject"]["arguments_digest"] == digest(
        {"key": "original", "choice": "production"}
    )

    def envelope(command):
        return CommandEnvelope(
            command_id=command.command_id,
            command_type=command.command_type,
            command_schema_version=command.command_schema_version,
            stream_type="run",
            stream_id=str(state.run_id),
            expected_stream_version=state.stream_version,
            owner_user_id="user-1",
            team_id=None,
            correlation_id=UUID(int=9),
            causation_id=None,
            issued_at=NOW,
            payload=command.payload,
        )

    event = RunAggregate().decide(selected, envelope(preview)).events[0]
    assert "subject" not in event.public_payload
    assert event.internal_payload["subject"]["authorization_epoch"] == subject.authorization_epoch
    assert event.public_payload["subject_digest"] == preview.payload["subject_digest"]
    approved = selected.model_copy(
        update={
            "approval_decisions": (
                *selected.approval_decisions,
                (UUID(preview.payload["approval_id"]), "approved", "do not mutate"),
            ),
            "approval_subjects": (
                (
                    UUID(preview.payload["approval_id"]),
                    UUID(preview.payload["subject_activity_id"]),
                    preview.payload["subject_digest"],
                ),
            ),
        }
    )
    activity = next_command(
        approved, run_execution_context(approved), outcomes={model_id: data}, now=NOW
    )
    assert activity.command_schema_version == 4
    assert activity.payload["input_payload"]["tool_call"]["arguments"] == {
        "key": "original",
        "choice": "production",
    }
    assert "approval_feedback" not in activity.payload["input_payload"]
    assert RunAggregate().decide(approved, envelope(activity)).activity_requests


@pytest.mark.asyncio
async def test_connector_revision_excludes_other_tool_policy_but_binds_selected_policy_and_route():
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_evaluation_recording_repository import (
        DBEvaluationRecordingRepository,
    )

    scope = OwnerScope.personal("user-1")
    row = {
        "id": "connector",
        "url": "https://example.com/mcp",
        "enabled": True,
        "tool_policies": {"selected": {"effect": "read_only"}, "other": {"effect": "read_only"}},
        "updated_at": "before",
    }
    session = SimpleNamespace(scalar=AsyncMock(return_value=row))
    repo = DBEvaluationRecordingRepository(session)
    original = await repo.connector_binding(
        scope, "mcp", "connector", lock=True, execution=True, tool_name="selected"
    )
    row["tool_policies"]["other"] = {"effect": "external_write"}
    row["updated_at"] = "after"
    assert (
        await repo.connector_binding(
            scope, "mcp", "connector", lock=True, execution=True, tool_name="selected"
        )
        == original
    )
    row["tool_policies"]["selected"] = {"effect": "external_write"}
    assert (
        await repo.connector_binding(
            scope, "mcp", "connector", execution=True, tool_name="selected"
        )
        != original
    )
    assert "FOR SHARE" in str(session.scalar.call_args_list[0].args[0])
    row["tool_policies"]["selected"] = {"effect": "read_only"}
    row["url"] = "https://changed.example/mcp"
    assert (
        await repo.connector_binding(
            scope, "mcp", "connector", execution=True, tool_name="selected"
        )
        != original
    )


def test_untrusted_remote_safe_hint_never_promotes_recovery():
    descriptor = _Read().get_tool_descriptors()[0]
    value = RecordedContract(
        name=descriptor.name,
        pack="mcp",
        schema_body=descriptor.schema,
        policy=READ_SAFE,
        connector_id="server",
        binding_revision="binding",
        authority_revision="binding",
    )
    contract = execution_contract(value)
    assert not contract.recovery_safe
    assert contract.effect_contract.capability == "manual_only"


@pytest.mark.asyncio
async def test_server_owned_feedback_is_removed_from_model_arguments():
    context, _, catalog, _, _, _ = await _setup(_Read())
    snapshot = await catalog.definitions({}, context)
    definition = snapshot.definitions[0].model_copy(update={"approval_feedback_param": "choice"})
    calls = _normalize_tool_calls(
        [
            {
                "id": "call",
                "function": {
                    "name": definition.name,
                    "arguments": {"key": "value", "choice": "impersonated user"},
                },
            }
        ],
        (definition,),
    )
    assert calls[0]["arguments"] == {"key": "value"}


@pytest.mark.asyncio
async def test_current_contract_authority_rejection_is_not_sent_not_transport_unknown():
    from app.domain.evaluation.errors import ReplayMismatch

    pack = _Write()
    context, _, catalog, _, objects, request = await _setup(pack)
    catalog._contract_capture = SimpleNamespace(
        describe=AsyncMock(side_effect=ReplayMismatch("capture_binding_changed"))
    )
    outcome = await ToolCallActivityHandler(
        objects=objects,
        tools=catalog,
        effects=SimpleNamespace(invoke=AsyncMock(), recover=AsyncMock(return_value=None)),
    ).execute(request, context)
    assert outcome.status == "failed"
    assert outcome.failure_code == "TOOL_CONTRACT_CHANGED"
    assert pack.sends == []


def test_clarification_label_does_not_exempt_a_remote_write_from_the_journal():
    descriptor = _Write().get_tool_descriptors()[0]
    contract = execution_contract(
        RecordedContract(
            name=descriptor.name,
            pack="mcp",
            schema_body=descriptor.schema,
            policy=EXTERNAL_WRITE.model_copy(
                update={"approval_kind": "clarification", "approval_feedback_param": "choice"}
            ),
            connector_id="server",
            binding_revision="binding",
            authority_revision="binding",
        )
    )
    assert contract.requires_operation
    assert not contract.is_pure_clarification


@pytest.mark.asyncio
async def test_deleted_skill_is_no_send_authorization_change_before_effect_prepare():
    from tests.app.application.execution.test_agent_tool_catalog import _catalog

    context, _, _, _, objects, request = await _setup(_Write())
    catalog, _, _ = _catalog(None)
    original = objects.load_input

    async def load_input(*, key, expected_digest):
        value = await original(key=key, expected_digest=expected_digest)
        return {**value, "skill_id": "deleted"} if key == request.input_ref else value

    objects.load_input = load_input
    effects = SimpleNamespace(invoke=AsyncMock(), recover=AsyncMock(return_value=None))
    outcome = await ToolCallActivityHandler(
        objects=objects, tools=catalog, effects=effects
    ).execute(request, context)
    assert outcome.status == "failed"
    assert outcome.failure_code == "TOOL_AUTHORIZATION_CHANGED"
    effects.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_infrastructure_failure_before_send_is_safe_failure_not_unknown():
    pack = _Write()
    context, _, catalog, _, objects, request = await _setup(pack)
    catalog._build = AsyncMock(side_effect=OSError("discovery unavailable"))
    effects = SimpleNamespace(invoke=AsyncMock(), recover=AsyncMock(return_value=None))
    outcome = await ToolCallActivityHandler(
        objects=objects, tools=catalog, effects=effects
    ).execute(request, context)
    assert outcome.status == "failed"
    assert outcome.failure_code == "TOOL_PREPARATION_FAILED"
    assert pack.sends == []
    effects.invoke.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["applied", "sending", "unknown", "pending"])
async def test_journal_recovery_precedes_catalog_and_never_repeats_transport(state):
    pack = _Write()
    context, _, catalog, _, objects, request = await _setup(pack)
    catalog._build = AsyncMock(side_effect=PermissionError("connector revoked after receipt"))
    stored = {"success": True, "data": {"receipt": "completed-before-worker-crash"}}
    effects = SimpleNamespace(
        recover=AsyncMock(
            return_value=SimpleNamespace(state=state, result=stored if state == "applied" else None)
        ),
        invoke=AsyncMock(),
    )
    handler = ToolCallActivityHandler(objects=objects, tools=catalog, effects=effects)
    assert await handler.recovery_safe(request, context.run)
    outcome = await handler.execute(request, context)
    assert outcome.status == ("succeeded" if state == "applied" else "unknown")
    if state == "applied":
        result = await objects.load_result(outcome.result_ref)
        assert result["kind"] == "tool"
        assert result["message"]["tool_call_id"] == "call-1"
        assert json.loads(result["message"]["content"]) == stored
    else:
        assert outcome.result_ref is None
    assert effects.recover.await_args.args[0].invocation_id == UUID(
        request.input_payload["subject"]["invocation_id"]
    )
    catalog._build.assert_not_called()
    effects.invoke.assert_not_called()
    assert pack.sends == []


def test_oversized_exact_approval_preview_fails_run_without_truncation_or_dispatch():
    from app.application.execution.decisions.base import request_approval
    from tests.app.application.execution.test_clarification_flow import _clarifying_model_round

    state, _, _, _ = _clarifying_model_round()
    decision = request_approval(
        state,
        activity_id=UUID(int=1),
        approval_id=UUID(int=2),
        approval_kind="tool_effect",
        risk_summary="Large exact write",
        subject_label="write_value",
        public_preview={"body": "x" * 131_073},
    )
    assert decision.command_type == "FailRun"
    assert decision.payload == {
        "failure_code": "TOOL_APPROVAL_PREVIEW_TOO_LARGE",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_a2a_effect_contract_binds_actual_card_route_not_presentation_text():
    from app.application.evaluation.contract_capture import ContractCapture
    from app.domain.models.integration_server import A2AServerRecord
    from app.domain.utils.integration_runtime_builder import a2a_records_to_runtime

    record = A2AServerRecord(id="remote", base_url="https://discovery.invalid")
    uow = SimpleNamespace(
        evaluation_recording=SimpleNamespace(connector_binding=AsyncMock(return_value="fixed")),
        a2a_server=SimpleNamespace(get_by_id=AsyncMock(return_value=record)),
    )

    @asynccontextmanager
    async def factory():
        yield uow

    card = {"url": "https://execute.invalid/one", "description": "untrusted description"}
    pack = SimpleNamespace(
        name="a2a",
        manager=SimpleNamespace(agent_cards={record.id: card}),
        recording_runtime=a2a_records_to_runtime([record]),
        get_tool_descriptors=lambda: [replace(_Write().get_tool_descriptors()[0], tool_pack="a2a")],
    )
    context = SimpleNamespace(run=SimpleNamespace(owner_scope="scope"))
    capture = ContractCapture(factory)
    built = SimpleNamespace(packs=[pack])
    original = (await capture.describe(context, built))[0]
    card["description"] = "another description"
    assert (await capture.describe(context, built))[0].digest == original.digest
    card["url"] = "https://execute.invalid/two"
    changed = (await capture.describe(context, built))[0]
    assert changed.digest != original.digest
    assert changed.connector_bindings == original.connector_bindings
    assert "execute.invalid" not in str(changed.model_dump(mode="json"))
