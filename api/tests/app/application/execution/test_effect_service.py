"""Public human evidence is usable, bound to its operation, and checked twice."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.execution.effect_service import EffectService
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.execution.effect import EffectOperationSpec, EffectState
from app.domain.models.scope import OwnerScope, Principal


def fixture():
    spec = EffectOperationSpec(
        operation_id=uuid4(),
        invocation_id=uuid4(),
        run_id=uuid4(),
        activity_id=uuid4(),
        generation=0,
        provider_identity="provider",
        provider_locator={"action": "update"},
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key="key",
        capability="manual_only",
        output_schema={
            "type": "object",
            "required": ["success"],
            "properties": {"success": {"type": "boolean"}},
        },
    )
    files = SimpleNamespace(get_file_info=AsyncMock(), read_fixed=AsyncMock())
    objects = SimpleNamespace(put_result=AsyncMock())
    store = SimpleNamespace(
        get=AsyncMock(),
        existing_request=AsyncMock(return_value=None),
        submit_request=AsyncMock(return_value={"status": "pending"}),
    )
    service = EffectService(store=store, objects=objects, files=files)
    return service, spec, files, store


def package(spec, *, include_result=False, **updates):
    value = {
        "schema_version": 1,
        "operation_id": str(spec.operation_id),
        "contract_digest": spec.contract_digest,
        "arguments_digest": spec.arguments_digest,
        "target_digest": spec.target_digest,
        "result" if include_result else "evidence": {"success": True}
        if include_result
        else {"filename": "audit.json", "content": "verified provider log"},
    }
    return {**value, **updates}


def bind_file(files, payload):
    raw = json.dumps(payload).encode()
    digest = hashlib.sha256(raw).hexdigest()
    files.get_file_info.return_value = SimpleNamespace(
        size=len(raw), object_identity="immutable", content_digest=digest
    )
    files.read_fixed.return_value = raw
    return {"ref": f"file://{uuid4()}", "sha256": digest}


def attestation(spec):
    return {
        "source": "provider audit",
        "action": "update",
        "target": spec.target_digest,
        "occurred_at": datetime.now(UTC).isoformat(),
        "method": "verified immutable log",
        "responsible_actor": "user",
    }


@pytest.mark.asyncio
async def test_human_evidence_reads_fixed_scoped_file_and_checks_full_binding():
    service, spec, files, _ = fixture()
    reference = bind_file(files, package(spec))
    body = {"decision": "applied", "evidence_refs": [reference], "attestation": attestation(spec)}
    packages, result = await service.verify_manual(
        spec, body, OwnerScope.personal("user"), Principal(user_id="user")
    )
    assert packages[0]["evidence"]["content"] == "verified provider log"
    assert result is None
    files.read_fixed.assert_awaited_once_with(
        reference["ref"][7:], reference["sha256"], "immutable", OwnerScope.personal("user")
    )
    bind_file(files, package(spec, target_digest="d" * 64))
    with pytest.raises(ValidationError):
        await service.verify_manual(
            spec, body, OwnerScope.personal("user"), Principal(user_id="user")
        )


@pytest.mark.asyncio
async def test_result_package_is_verified_against_frozen_output_schema():
    service, spec, files, _ = fixture()
    reference = bind_file(files, package(spec, include_result=True))
    result = await service._file_package(spec, reference, OwnerScope.personal("user"), result=True)
    assert result["result"] == {"success": True}
    reference = bind_file(
        files, package(spec, include_result=True, result={"success": "untrusted"})
    )
    with pytest.raises(ValidationError):
        await service._file_package(spec, reference, OwnerScope.personal("user"), result=True)


@pytest.mark.asyncio
async def test_successful_request_replay_precedes_terminal_and_source_file_checks():
    service, spec, files, store = fixture()
    existing = {"status": "completed", "request_id": uuid4()}
    store.existing_request.return_value = existing
    store.get.return_value = {
        "body": EffectState(spec=spec, state="applied").model_dump(mode="json")
    }
    kwargs = {
        "run_id": spec.run_id,
        "operation_id": spec.operation_id,
        "expected_revision": 3,
        "idempotency_key": "old-key",
        "scope": OwnerScope.personal("user"),
        "principal": Principal(user_id="user"),
        "scope_reason": None,
    }
    result = await service.submit_resolution(
        decision="applied",
        evidence_refs=[{"ref": "file://deleted", "sha256": "a" * 64}],
        reason="accepted already",
        attestation=attestation(spec),
        **kwargs,
    )
    assert result == existing
    files.read_fixed.assert_not_awaited()
    store.submit_request.assert_not_awaited()
    assert await service.submit_query(**kwargs) == existing


@pytest.mark.asyncio
async def test_revoked_file_fails_consumption_even_when_previously_accepted():
    service, spec, files, _ = fixture()
    reference = bind_file(files, package(spec))
    body = {"decision": "applied", "evidence_refs": [reference], "attestation": attestation(spec)}
    await service.verify_manual(spec, body, OwnerScope.personal("user"), Principal(user_id="user"))
    from app.domain.errors import NotFoundError

    files.get_file_info.side_effect = NotFoundError("revoked")
    with pytest.raises(ValidationError):
        await service.verify_manual(
            spec, body, OwnerScope.personal("user"), Principal(user_id="user")
        )


def test_member_and_auditor_can_read_without_resolution_actions():
    service, spec, _, _ = fixture()
    row = {"body": EffectState(spec=spec, state="unknown").model_dump(mode="json")}
    summary = service._summary(row)
    assert summary["allowed_actions"] == ["resolve", "abandon"]
    assert (
        service._read_actions(
            summary,
            Principal(user_id="member", team_roles={"team": "member"}),
            OwnerScope.team("member", "team"),
        )["allowed_actions"]
        == []
    )


class AggregateStore:
    """Exercise the real pure aggregate behind the application persistence port."""

    def __init__(self, spec):
        from app.domain.execution.run import RunAggregate, RunState, RunStatus

        self.aggregate = RunAggregate()
        self.state = RunState(
            run_id=spec.run_id,
            status=RunStatus.RUNNING,
            active_activity_ids=(spec.activity_id,),
            activity_generations=((spec.activity_id, 0),),
            owner_user_id="user",
        )
        self.events = []

    async def worker_command(self, command, *, preflight=None):
        if preflight:
            await preflight(object())
        from app.domain.execution.events import StoredEvent

        decision = self.aggregate.decide(self.state, command)
        for proposed in decision.events:
            version = self.state.stream_version + 1
            event = StoredEvent(
                **proposed.model_dump(),
                position=version,
                event_id=uuid4(),
                stream_type="run",
                stream_id=str(self.state.run_id),
                stream_version=version,
                owner_user_id="user",
                team_id=None,
                correlation_id=self.state.run_id,
                causation_id=None,
                occurred_at=datetime.now(UTC),
                prev_hash="0" * 64,
                event_hash="1" * 64,
            )
            self.state = self.aggregate.evolve(self.state, event)
            self.events.append(event)
        return SimpleNamespace(status="accepted")

    async def get(self, **kwargs):
        if not self.state.effect_operations:
            raise NotFoundError("External operation not found")
        return {"body": self.state.effect_operations[0].model_dump(mode="json")}


async def invocation_fixture():
    from app.application.execution.activity_inputs import ActivityObjectStore

    _, spec, _, _ = fixture()
    memory = {}

    async def put(key, value):
        memory[key] = value

    async def get(key):
        return memory[key]

    objects = ActivityObjectStore(SimpleNamespace(put_bytes=put, get_bytes=get))
    store = AggregateStore(spec)
    context = SimpleNamespace(
        activity_id=spec.activity_id,
        generation=spec.generation,
        require_active_lease=None,
        worker_id="worker",
        claim_generation=1,
        run=SimpleNamespace(run_id=spec.run_id, owner_scope=OwnerScope.personal("user")),
    )
    return EffectService(store=store, objects=objects, files=None), spec, context, store


@pytest.mark.asyncio
async def test_invoke_commits_send_intent_and_repeat_never_calls_transport_again():
    service, spec, context, store = await invocation_fixture()
    send = AsyncMock(return_value={"success": True, "data": "accepted"})
    preflight = AsyncMock()
    result = await service.invoke(spec, context, send, preflight=preflight)
    assert result.state == "applied"
    assert result.result == {"success": True, "data": "accepted"}
    repeated = await service.invoke(spec, context, send, preflight=preflight)
    assert repeated.result == result.result
    send.assert_awaited_once()
    assert preflight.await_count == 2
    assert [event.event_type for event in store.events] == [
        "EffectOperationPrepared",
        "EffectDispatchStarted",
        "EffectReceiptRecorded",
        "EffectOutcomeObserved",
    ]


@pytest.mark.asyncio
async def test_transport_or_false_result_preserves_unknown_and_never_retries_write():
    service, spec, context, store = await invocation_fixture()
    send = AsyncMock(side_effect=TimeoutError("request may already be applied"))
    result = await service.invoke(spec, context, send)
    assert result.state == "unknown"
    assert result.result is None
    assert store.state.effect_operations[0].latest_receipt.source == "transport_unknown"
    await service.invoke(spec, context, send)
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_preflight_failure_records_no_operation_and_calls_no_provider():
    service, spec, context, store = await invocation_fixture()
    send = AsyncMock()
    preflight = AsyncMock(side_effect=PermissionError("authority revoked"))
    with pytest.raises(PermissionError):
        await service.invoke(spec, context, send, preflight=preflight)
    send.assert_not_awaited()
    assert store.state.effect_operations == ()
    assert store.events == []


@pytest.mark.asyncio
async def test_revocation_between_prepare_and_dispatch_preserves_prepared_without_sending():
    service, spec, context, store = await invocation_fixture()
    send = AsyncMock()
    preflight = AsyncMock(side_effect=[None, PermissionError("authority revoked")])
    with pytest.raises(PermissionError):
        await service.invoke(spec, context, send, preflight=preflight)
    send.assert_not_awaited()
    assert preflight.await_count == 2
    assert store.state.effect_operations[0].state == "prepared"
    assert [event.event_type for event in store.events] == ["EffectOperationPrepared"]


@pytest.mark.asyncio
async def test_committed_result_recovery_does_not_require_a_new_dispatch_authorization():
    service, spec, context, _ = await invocation_fixture()
    send = AsyncMock(return_value={"success": True})
    await service.invoke(spec, context, send)
    preflight = AsyncMock(side_effect=PermissionError("connector authorization revoked"))
    recovered = await service.invoke(spec, context, send, preflight=preflight)
    assert recovered.state == "applied"
    assert recovered.result == {"success": True}
    preflight.assert_not_awaited()
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_sending_claim_retains_receipt_only_for_trusted_reconciliation():
    from app.domain.errors import ConflictError

    service, spec, context, store = await invocation_fixture()
    original = store.worker_command

    async def fenced(command, *, preflight=None):
        if command.command_type == "RecordEffectObservation":
            raise ConflictError("claim expired")
        return await original(command, preflight=preflight)

    store.worker_command = fenced
    store.defer_observation = AsyncMock()
    result = await service.invoke(spec, context, AsyncMock(return_value={"success": True}))
    assert result.state == "unknown"
    assert store.state.effect_operations[0].state == "sending"
    assert [event.event_type for event in store.events] == [
        "EffectOperationPrepared",
        "EffectDispatchStarted",
    ]
    store.defer_observation.assert_awaited_once()
    assert store.defer_observation.call_args.args[2].state == "applied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [None, "prepared", "sending", "pending", "unknown", "not_applied"]
)
async def test_recover_only_reads_scoped_journal_and_never_creates_send_intent(state):
    service, spec, context, _ = await invocation_fixture()
    store = SimpleNamespace(get=AsyncMock(), worker_command=AsyncMock())
    service.store = store
    if state is None:
        store.get.side_effect = NotFoundError("missing")
    else:
        store.get.return_value = {
            "body": EffectState(spec=spec, state=state).model_dump(mode="json")
        }
    recovered = await service.recover(spec, context)
    if state in {None, "prepared"}:
        assert recovered is None
    else:
        assert recovered.state == state
        assert recovered.result is None
    store.get.assert_awaited_once_with(
        operation_id=spec.operation_id, run_id=spec.run_id, scope=context.run.owner_scope
    )
    store.worker_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_receipt_after_worker_crash_unwraps_frozen_tool_result_without_catalog():
    service, spec, context, store = await invocation_fixture()
    spec = spec.model_copy(
        update={"provider_locator": {"tool_call_id": "call-1", "tool_name": "update"}}
    )
    send = AsyncMock(return_value={"success": True, "data": "accepted"})
    await service.invoke(spec, context, send)
    event_count = len(store.events)
    # A later worker can finish using the immutable receipt even if connector
    # discovery and the original send function no longer exist.
    store.worker_command = AsyncMock(side_effect=AssertionError("recovery must only read"))
    recovered = await service.recover(spec, context)
    assert recovered.state == "applied"
    assert recovered.result == {"success": True, "data": "accepted"}
    assert recovered.result_ref == store.state.effect_operations[0].result_ref
    assert len(store.events) == event_count
    store.worker_command.assert_not_awaited()
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["integrity_conflict", "abandoned"])
async def test_recovery_never_adopts_a_quarantined_or_abandoned_result(flag):
    service, spec, context, store = await invocation_fixture()
    await service.invoke(spec, context, AsyncMock(return_value={"success": True}))
    effect = store.state.effect_operations[0].model_copy(update={flag: True})
    store.get = AsyncMock(return_value={"body": effect.model_dump(mode="json")})
    service.objects.load_result = AsyncMock(side_effect=AssertionError("must not adopt result"))
    result = await service.recover(spec, context)
    assert result.state == "unknown"
    assert result.result is None
    assert result.result_ref is None
    service.objects.load_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("arguments_digest", "d" * 64), ("contract_digest", "e" * 64), ("invocation_id", uuid4())],
)
async def test_recover_rejects_another_frozen_intent(field, value):
    service, spec, context, _ = await invocation_fixture()
    await service.invoke(spec, context, AsyncMock(return_value={"success": True}))
    with pytest.raises(ConflictError, match="another invocation"):
        await service.recover(spec.model_copy(update={field: value}), context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"), [("run_id", uuid4()), ("activity_id", uuid4()), ("generation", 1)]
)
async def test_recover_rejects_context_mismatch_before_reading_result(field, value):
    service, spec, context, store = await invocation_fixture()
    store.get = AsyncMock()
    with pytest.raises(ConflictError, match="Activity context"):
        await service.recover(spec.model_copy(update={field: value}), context)
    store.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["tool_identity", "output_schema", "activity_ref"])
async def test_recover_fails_closed_for_misbound_or_invalid_persisted_result(corruption):
    from app.application.execution.effect_service import result_object

    service, spec, context, store = await invocation_fixture()
    spec = spec.model_copy(
        update={"provider_locator": {"tool_call_id": "call-1", "tool_name": "update"}}
    )
    value = result_object(spec, {"success": "invalid" if corruption == "output_schema" else True})
    if corruption == "tool_identity":
        value["message"]["tool_call_id"] = "other-call"
    result_ref = await service.objects.put_result(
        uuid4() if corruption == "activity_ref" else spec.activity_id, value
    )
    store.get = AsyncMock(
        return_value={
            "body": EffectState(spec=spec, state="applied", result_ref=result_ref).model_dump(
                mode="json"
            )
        }
    )
    with pytest.raises(ValidationError, match="Persisted effect result"):
        await service.recover(spec, context)
