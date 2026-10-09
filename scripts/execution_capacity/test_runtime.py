"""Production worker/handler composition with in-memory external boundaries only."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from scripts.execution_capacity.observers import (
    ObservedContent,
    ObservedHandler,
    ObservedStorage,
    RecoveryJournal,
)
from scripts.execution_capacity.runtime import FencedClaims
from scripts.execution_capacity.test_observers import Storage

from app.application.execution.activities.retrieval import RetrievalActivityHandler
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.orchestrator import CommandResult
from app.application.execution.run_service import RunService
from app.domain.execution.activity import ActivityClaim, ActivityRequest
from tests.app.execution_test_support import run_execution_context_for


def test_production_retrieval_worker_uses_actual_objects_and_stable_commands(tmp_path):
    tmp_path.chmod(0o700)

    async def exercise(journal):
        run, activity = uuid4(), uuid4()
        journal.intent("run", run, {"scope": "user:user-1"})
        journal.intent("activity", activity, {"scope": "user:user-1", "run_id": str(run)})
        storage = Storage()
        objects = ActivityObjectStore(ObservedStorage(storage, journal))
        ref, digest = await objects.put_input(
            run, {"message": "query", "session_id": "unit-session", "mode": "ask"}
        )
        claim = ActivityClaim(
            request=ActivityRequest(
                activity_id=activity,
                activity_type="retrieval.search",
                aggregate_type="run",
                aggregate_id=str(run),
                generation=0,
                timeout_at=datetime.now(UTC) + timedelta(minutes=1),
                input_ref=ref,
                input_digest=digest,
            ),
            claim_generation=7,
            owner_user_id="user-1",
            team_id=None,
        )
        calls = []

        class Memories:
            async def recall_for_session(self, session_id, **kwargs):
                calls.append(("memory", session_id))
                return ""

        class Catalog:
            async def retrieve(self, payload, context, *, query):
                calls.append(("retrieval", query))
                return {"query": query, "sources": []}

        class Store:
            async def claim(self, **kwargs):
                return (claim,)

            async def mark_call_started(self, actual, **kwargs):
                calls.append(("mark_started", actual.claim_generation))
                return True

            async def heartbeat(self, actual, **kwargs):
                return True

            async def defer(self, *args, **kwargs):
                raise AssertionError("successful unit retrieval cannot defer")

        class Contexts:
            async def load(self, run_id):
                return run_execution_context_for("ask", run_id=run_id)

        class Handler:
            def __init__(self):
                self.commands = []

            async def handle(self, envelope):
                assert journal.get("command", envelope.command_id) is not None
                self.commands.append(envelope)
                return CommandResult(
                    command_id=envelope.command_id,
                    status="accepted",
                    first_event_position=len(self.commands),
                    last_event_position=len(self.commands),
                    rejection_code=None,
                )

        class Content:
            async def prepare(self, actual, command_id, command_type, payload, **kwargs):
                phase = "input" if command_type == "MarkActivityCallStarted" else "output"
                assert journal.get("content", f"{command_id}:{phase}") is not None
                calls.append(("content", phase))

        class Gate:
            async def before_activity(self, actual, context):
                calls.append(("gate", actual.request.activity_id))

        handler = Handler()
        registry = ActivityRegistry()
        registry.register(
            RetrievalActivityHandler(objects=objects, tools=Catalog(), memories=Memories())
        )
        worker = ActivityWorker(
            store=Store(),
            run_contexts=Contexts(),
            run_service=RunService(orchestrator=ObservedHandler(handler, journal)),
            registry=registry,
            worker_id="unit",
            content_writer=ObservedContent(Content(), journal),
            execution_gate=Gate(),
        )
        stats = await worker.run_once(now=datetime.now(UTC), limit=1)
        assert stats.succeeded == 1
        assert calls.index(("gate", activity)) < calls.index(("mark_started", 7))
        assert [c.command_type for c in handler.commands] == [
            "MarkActivityCallStarted",
            "CompleteActivity",
        ]
        assert handler.commands[0].command_id == uuid5(
            NAMESPACE_URL, f"opencitadel:{activity}:MarkActivityCallStarted:0:7"
        )
        assert handler.commands[1].command_id == uuid5(
            NAMESPACE_URL, f"opencitadel:{activity}:CompleteActivity"
        )
        result = await objects.load_result(handler.commands[1].payload["result_ref"])
        assert result["kind"] == "retrieval"
        assert ("memory", "unit-session") in calls
        assert ("retrieval", "query") in calls
        assert (
            calls.index(("content", "input"))
            < calls.index(("retrieval", "query"))
            < calls.index(("content", "output"))
        )

    with RecoveryJournal(tmp_path) as journal:
        asyncio.run(exercise(journal))


def test_claim_recovery_refuses_new_generation_before_delegate():
    class Facts:
        async def assert_exclusive(self):
            pass

        async def task(self, _):
            return {"status": "pending", "claim_generation": 1}

    class Store:
        async def claim(self, **kwargs):
            raise AssertionError("must not reclaim")

    with pytest.raises(ValueError, match="claimed again"):
        asyncio.run(FencedClaims(Store(), Facts(), uuid4()).claim(limit=1))


def test_shared_object_adapter_default_and_wrapper_are_constructor_bound(monkeypatch):
    from app.composition.shared import _object_storage

    real, wrapped = object(), object()
    monkeypatch.setattr(
        "app.composition.shared.create_object_storage_adapter", lambda **kwargs: real
    )
    resources = SimpleNamespace(
        settings=SimpleNamespace(storage_provider="minio"), object_storage_client=object()
    )
    assert _object_storage(resources) is real
    seen = []

    def wrapper(adapter):
        seen.append(adapter)
        return wrapped

    assert _object_storage(resources, wrapper=wrapper) is wrapped
    assert seen == [real]


def test_runtime_request_binds_admitted_input_and_current_pinned_timeout():
    from scripts.execution_capacity.runtime import runtime_request
    from scripts.test_benchmark_execution_visualization import _plan

    plan = _plan(1000)
    request = next(
        command.envelope for command in plan.commands() if command.command_type == "RequestActivity"
    )
    now = datetime.now(UTC)
    actual = runtime_request(
        request,
        {"input_ref": "execution/inputs/exact/digest.json", "input_digest": "a" * 64},
        plan.policy,
        now,
    )
    assert datetime.fromisoformat(actual.payload["timeout_at"]) == now + timedelta(
        seconds=plan.policy.common.activity.tool_timeout_seconds
    )
    assert actual.issued_at == now
    assert actual.payload["input_ref"] == "execution/inputs/exact/digest.json"
    assert actual.payload["input_digest"] == "a" * 64
    assert actual.payload["input_payload"] == {}
    assert actual.command_id == request.command_id
