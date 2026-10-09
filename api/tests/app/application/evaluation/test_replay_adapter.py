from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.evaluation.replay_adapter import ReplayAdapter
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import (
    MatchRule,
    RecordedContract,
    RecordingSlot,
    canonical,
    recording_key,
)
from app.domain.models.tool_policy import CONSERVATIVE_TOOL_POLICY


@pytest.mark.asyncio
async def test_approval_precedes_body_and_ledger_then_exact_retry():
    import hashlib

    contract = RecordedContract(
        name="write",
        pack="test",
        schema_body={
            "function": {
                "parameters": {
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                    "required": ["n"],
                }
            }
        },
        policy=CONSERVATIVE_TOOL_POLICY,
        binding_revision="1",
        authority_revision="1",
    )
    data = canonical({"success": True})
    slot = RecordingSlot(
        id=uuid4(),
        tool="write",
        contract_digest=contract.digest,
        match_key=recording_key("write", contract.digest, {"n": 1}, "root", 0),
        rule=MatchRule(),
        branch="root",
        ordinal=0,
        object_id=uuid4(),
        result_digest=hashlib.sha256(data).hexdigest(),
        result_bytes=len(data),
        simulated_effect=True,
    )
    events, ledger = [], {}

    class Repo:
        async def lock_call(self, *args):
            events.append("lock")

        async def consumed(self, *args):
            return ledger.get("call")

        async def object(self, *args):
            events.append("object")
            return {"storage_key": "fixed", "digest": slot.result_digest, "size_bytes": len(data)}

        async def consume(self, *args):
            events.append("consume")
            ledger["call"] = {
                "match_key": slot.match_key,
                "slot_id": slot.id,
                "version_id": version,
            }

    version = uuid4()
    manifest = SimpleNamespace(id=version, revision=1, contracts=(contract,), slots=(slot,))

    class Authority:
        approved = False

        @asynccontextmanager
        async def open(self, context):
            yield SimpleNamespace(
                scope="scope",
                manifest=manifest,
                repo=Repo(),
                uow=SimpleNamespace(commit=self.commit),
            )

        async def commit(self):
            events.append("commit")

        async def approve(self, *args):
            events.append("approve")
            if not self.approved:
                raise ReplayMismatch("approval_required")

    class Objects:
        async def get_bytes(self, key):
            events.append("body")
            return data

    authority = Authority()
    adapter = ReplayAdapter(authority, Objects())
    context = SimpleNamespace(activity_id=uuid4(), run=SimpleNamespace(run_id=uuid4()))
    with pytest.raises(ReplayMismatch, match="approval_required"):
        await adapter.match(context, "write", contract.digest, {"n": 1}, "root", 0)
    assert events == ["approve"]
    authority.approved = True
    first = await adapter.match(context, "write", contract.digest, {"n": 1}, "root", 0)
    second = await adapter.match(context, "write", contract.digest, {"n": 1}, "root", 0)
    assert first == second
    assert first.simulated_effect
    assert events.count("consume") == 1
    with pytest.raises(ReplayMismatch):
        await adapter.match(context, "write", contract.digest, {"n": 2}, "root", 0)
    assert events.count("consume") == 1


@pytest.mark.asyncio
async def test_catalog_replay_definitions_and_direct_guards_never_build():
    from app.application.execution.agent_tool_catalog import AgentToolCatalog
    from app.application.execution.tool_catalog import CatalogSnapshot

    catalog = object.__new__(AgentToolCatalog)
    expected = CatalogSnapshot(definitions=(), fingerprint="fixed")

    class Replay:
        async def active(self, context):
            return True

        async def definitions(self, context):
            return expected

    catalog._replay = Replay()

    async def forbidden(*args, **kwargs):
        raise AssertionError("real catalog build invoked")

    catalog._build = forbidden
    assert await catalog.definitions({}, object()) == expected
    with pytest.raises(ReplayMismatch):
        await catalog.invoke({}, object(), name="t", arguments={})
    with pytest.raises(ReplayMismatch):
        await catalog.retrieve({}, object(), query="q")


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True])
async def test_real_worker_recovery_only_trusted_binding_is_idempotent(trusted):
    from app.application.execution.activity_registry import ActivityRegistry
    from app.application.execution.activity_worker import ActivityWorker
    from tests.app.application.execution.test_activity_worker import (
        NOW,
        FakeRunContexts,
        FakeRunService,
        FakeStore,
        Handler,
        claim,
    )

    class ReplayHandler(Handler):
        async def recovery_safe(self, request, run):
            return trusted

    handler = ReplayHandler(idempotent=False)
    registry = ActivityRegistry()
    registry.register(handler)
    worker = ActivityWorker(
        store=FakeStore((claim(recovered=True),)),
        run_service=FakeRunService(),
        run_contexts=FakeRunContexts(),
        registry=registry,
        worker_id="replay-test",
    )
    stats = await worker.run_once(now=NOW, limit=1)
    assert stats.succeeded == int(trusted)
    assert stats.unknown == int(not trusted)
    assert len(handler.calls) == int(trusted)


def test_replay_failure_is_fatal_despite_positive_retry_budget():
    from app.application.execution.decisions.base import fail_for_activity
    from app.domain.execution.run import RunState

    identity = uuid4()
    state = RunState(run_id=uuid4(), activity_failure_codes=((identity, 0, "REPLAY_MISMATCH"),))
    result = fail_for_activity(state, "failed", activity_id=identity, max_retries=5)
    assert result.payload["failure_code"] == "REPLAY_MISMATCH"
    assert result.payload["retryable"] is False


@pytest.mark.asyncio
async def test_production_redacted_schema_capture_is_explicit_unavailable_not_failure():
    from app.application.evaluation.contract_capture import ContractCapture
    from app.domain.models.tool_policy import CONSERVATIVE_TOOL_POLICY

    writes = []

    class Repo:
        async def capture(self, *args):
            writes.append(args[-1])

    class Work:
        evaluation_recording = Repo()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

    descriptor = SimpleNamespace(
        schema={"function": {"parameters": {"properties": {"password": {"type": "string"}}}}},
        name="test",
        policy=CONSERVATIVE_TOOL_POLICY,
        tool_pack="test",
    )
    pack = SimpleNamespace(name="test", get_tool_descriptors=lambda: [descriptor])
    await ContractCapture(Work).capture(
        SimpleNamespace(
            activity_id=uuid4(), run=SimpleNamespace(run_id=uuid4(), owner_scope="scope")
        ),
        SimpleNamespace(packs=[pack], fingerprint="one"),
    )
    assert writes == [{"unavailable": "capture_schema_redacted"}]


@pytest.mark.asyncio
async def test_disabled_tool_catalog_is_captured_as_an_explicit_empty_catalog():
    from app.application.evaluation.contract_capture import ContractCapture

    writes = []

    class Repo:
        async def capture(self, scope, run_id, activity_id, body):
            writes.append((scope, run_id, activity_id, body))

    class Work:
        evaluation_recording = Repo()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

    run_id, activity_id = uuid4(), uuid4()
    await ContractCapture(Work).capture_disabled(
        SimpleNamespace(
            activity_id=activity_id,
            run=SimpleNamespace(run_id=run_id, owner_scope="scope"),
        )
    )
    assert len(writes) == 1
    assert writes[0][:3] == ("scope", run_id, activity_id)
    assert writes[0][3]["contracts"] == []
    assert len(writes[0][3]["fingerprint"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_real_tool_handler_replay_never_calls_real_tool(mismatch):
    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from tests.app.application.execution.test_tool_contract_v2 import CONTEXT, _Objects, _request

    calls = []

    class RealTools:
        async def invoke(self, *args, **kwargs):
            calls.append("real")
            raise AssertionError("real tool")

    class Replay:
        async def active(self, context):
            return True

        async def tool(self, request, context):
            if mismatch:
                raise ReplayMismatch("arguments_mismatch")
            return {"success": True, "simulated_effect": True, "recording_revision": 1}

    objects = _Objects()
    result = await ToolCallActivityHandler(
        objects=objects, tools=RealTools(), replay=Replay()
    ).execute(_request(name="write", arguments={}), CONTEXT)
    assert calls == []
    if mismatch:
        assert result.status == "failed"
        assert result.failure_code == "REPLAY_MISMATCH"
        assert objects.written == []
    else:
        assert result.status == "succeeded"
        assert "simulated_effect" in result.public_data["content"]


@pytest.mark.asyncio
async def test_marked_run_without_trusted_binding_fails_before_any_fallback():
    from app.application.evaluation.replay_runtime import ReplayRuntime

    class Authority:
        async def binding(self, run):
            return None

    runtime = ReplayRuntime(Authority(), None, None)
    run = SimpleNamespace(source_entity_type="evaluation_recorded_case")
    with pytest.raises(ReplayMismatch, match="replay_binding_missing"):
        await runtime.active(SimpleNamespace(run=run))
    with pytest.raises(ReplayMismatch, match="replay_binding_missing"):
        await runtime.recovery_safe(None, run)
    assert not await runtime.active(
        SimpleNamespace(run=SimpleNamespace(source_entity_type="session"))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_capture_binds_actual_initialized_connector_and_explicit_source(changed):
    from app.application.evaluation.contract_capture import ContractCapture
    from app.domain.models.integration_server import MCPServerRecord
    from app.domain.services.tools.capability_policy import READ_SAFE
    from app.domain.utils.integration_runtime_builder import mcp_records_to_runtime

    record = MCPServerRecord(
        id="connector-id", name="display name", url="http://never-called.invalid/mcp"
    )
    current = record.model_copy(update={"url": "http://changed.invalid/mcp"}) if changed else record
    stored = []

    class Repo:
        async def connector_binding(self, scope, pack, identity, *, lock):
            assert lock
            assert identity == record.id
            return "revision"

        async def capture(self, *args):
            stored.append(args[-1])

    class Servers:
        async def get_by_id(self, identity, *, scope):
            return current

    class Work:
        evaluation_recording, mcp_server = Repo(), Servers()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

    descriptor = SimpleNamespace(
        name="unparseable_name",
        schema={"function": {"name": "unparseable_name", "parameters": {"type": "object"}}},
        policy=READ_SAFE,
        tool_pack="mcp",
    )
    pack = SimpleNamespace(
        name="mcp",
        recording_runtime=mcp_records_to_runtime([record]),
        recording_source=lambda name: (record.id, "actual_remote_tool"),
        get_tool_descriptors=lambda: [descriptor],
    )
    await ContractCapture(Work).capture(
        SimpleNamespace(
            activity_id=uuid4(), run=SimpleNamespace(run_id=uuid4(), owner_scope="scope")
        ),
        SimpleNamespace(packs=[pack], fingerprint="fixed"),
    )
    if changed:
        assert stored == [{"unavailable": "capture_binding_changed"}]
    else:
        assert stored[0]["contracts"][0]["source_name"] == "actual_remote_tool"
        assert stored[0]["contracts"][0]["connector_bindings"] == {"connector-id": "revision"}
        assert "never-called" not in str(stored)


@pytest.mark.asyncio
async def test_same_arguments_same_ordinal_have_isolated_round_and_branch_slots():
    import hashlib

    from app.domain.services.tools.capability_policy import READ_SAFE

    contract = RecordedContract(
        name="read",
        pack="test",
        schema_body={"function": {"parameters": {"type": "object"}}},
        policy=READ_SAFE,
        binding_revision="1",
        authority_revision="1",
    )
    locators = [("root", "round:0"), ("root", "round:1"), ("child", "round:0")]
    bodies, slots, ledger = {}, [], {}
    for branch, group in locators:
        object_id = uuid4()
        body = canonical({"result": branch + group})
        bodies[str(object_id)] = body
        slots.append(
            RecordingSlot(
                id=uuid4(),
                tool="read",
                contract_digest=contract.digest,
                match_key=recording_key(
                    "read", contract.digest, {}, branch, 0, parallel_group=group
                ),
                rule=MatchRule(),
                branch=branch,
                parallel_group=group,
                ordinal=0,
                object_id=object_id,
                result_digest=hashlib.sha256(body).hexdigest(),
                result_bytes=len(body),
                simulated_effect=False,
            )
        )
    version = uuid4()

    class Repo:
        async def lock_call(self, *args):
            pass

        async def consumed(self, scope, run, activity):
            return ledger.get(activity)

        async def object(self, scope, identity):
            body = bodies[str(identity)]
            return {
                "storage_key": str(identity),
                "digest": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
            }

        async def consume(self, scope, run, activity, version, slot):
            assert slot.id not in [row["slot_id"] for row in ledger.values()]
            ledger[activity] = {
                "slot_id": slot.id,
                "match_key": slot.match_key,
                "version_id": version,
            }

    class Authority:
        @asynccontextmanager
        async def open(self, context):
            yield SimpleNamespace(
                scope="scope",
                manifest=SimpleNamespace(
                    id=version, revision=1, contracts=(contract,), slots=tuple(slots)
                ),
                repo=Repo(),
                uow=self,
            )

        async def approve(self, *args):
            pass

        async def commit(self):
            pass

    class Objects:
        async def get_bytes(self, key):
            return bodies[key]

    adapter = ReplayAdapter(Authority(), Objects())
    results = []
    for branch, group in locators:
        context = SimpleNamespace(activity_id=uuid4(), run=SimpleNamespace(run_id=uuid4()))
        result = await adapter.match(
            context, "read", contract.digest, {}, branch, 0, parallel_group=group
        )
        assert (
            await adapter.match(
                context, "read", contract.digest, {}, branch, 0, parallel_group=group
            )
            == result
        )
        results.append(result.result_ref)
    assert len(set(results)) == len(locators)
    assert len(ledger) == len(locators)
