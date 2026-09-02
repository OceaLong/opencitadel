"""Application-level command transaction behavior."""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.kernel.application.command_service import CommandService
from app.kernel.application.ports import CommandResult, CommandResultStatus, KernelAuthorization
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.domain.workflows.agent import agent_reducer

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
COMMAND_ID = UUID(int=3001)
RUN_ID = UUID(int=3002)


class MemoryTransaction:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self.pending = copy.deepcopy(state)
        self.append_calls = 0

    async def get_command_result(self, command_id: UUID):
        return self.pending["results"].get(command_id)

    async def reserve_command(self, command: CommandEnvelope):
        self.pending["reserved"].append(command.command_id)

    async def load_events(self, run_id: UUID):
        return tuple(self.pending["events"].get(run_id, ()))

    async def validate_command(self, command: CommandEnvelope):
        return None

    async def append_decision(self, command, decision, facts):
        self.append_calls += 1
        result = CommandResult(
            command_id=command.command_id,
            run_id=command.run_id,
            status=CommandResultStatus.SUCCEEDED,
            stream_version=len(decision.events),
            event_ids=tuple(event.event_id for event in decision.events),
        )
        self.pending["results"][command.command_id] = result
        return result

    async def reject_command(self, command, *, code: str, message: str):
        result = CommandResult(
            command_id=command.command_id,
            run_id=command.run_id,
            status=CommandResultStatus.REJECTED,
            stream_version=0,
            error_code=code,
            error_message=message,
        )
        self.pending["results"][command.command_id] = result
        return result


class MemoryStore:
    def __init__(self) -> None:
        self.state: dict[str, object] = {
            "results": {},
            "reserved": [],
            "events": {},
        }
        self.transactions = 0
        self.append_calls = 0
        self.fail_after_yield = False

    @asynccontextmanager
    async def transaction(self, authorization):
        self.transactions += 1
        transaction = MemoryTransaction(self.state)
        try:
            yield transaction
            if self.fail_after_yield:
                raise RuntimeError("commit failed")
        except BaseException:
            raise
        else:
            self.state = transaction.pending
            self.append_calls += transaction.append_calls


def _command(*, prompt: str = "inspect") -> CommandEnvelope:
    return CommandEnvelope(
        command_id=COMMAND_ID,
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        type="StartAgent",
        payload={"title": "Audit", "prompt": prompt, "tool_catalog": []},
        expected_stream_version=0,
        owner_scope=OwnerScopeRef.personal("user-1"),
        actor_user_id="user-1",
        request_id="request-1",
        submitted_at=NOW,
    )


def _facts(command, state) -> DecisionFacts:
    return DecisionFacts(
        now=NOW,
        actor_user_id=command.actor_user_id,
        request_id=command.request_id,
        policy_revision_id=UUID(int=3010),
        event_ids=(UUID(int=3020), UUID(int=3021), UUID(int=3022)),
        effect_ids=(UUID(int=3030),),
    )


def _service(store: MemoryStore) -> CommandService:
    return CommandService(
        store=store,
        reducers=ReducerRegistry({Workflow.AGENT: agent_reducer}),
        facts_factory=_facts,
    )


@pytest.mark.asyncio
async def test_duplicate_command_returns_persisted_result_without_reducing_twice() -> None:
    """A client retry must not append a second event/effect sequence."""

    store = MemoryStore()
    service = _service(store)
    authorization = KernelAuthorization.for_user("user-1", OwnerScopeRef.personal("user-1"))

    first = await service.submit(_command(), authorization)
    second = await service.submit(_command(), authorization)

    assert second == first
    assert store.append_calls == 1
    assert store.state["reserved"] == [COMMAND_ID]


@pytest.mark.asyncio
async def test_authorization_scope_is_checked_before_reserving_command() -> None:
    """A cross-tenant command must leave no inbox or journal residue."""

    store = MemoryStore()
    service = _service(store)
    authorization = KernelAuthorization.for_user("user-2", OwnerScopeRef.personal("user-2"))
    cross_scope_command = _command().model_copy(update={"actor_user_id": "user-2"})

    with pytest.raises(PermissionError, match="owner scope"):
        await service.submit(cross_scope_command, authorization)

    assert store.state["reserved"] == []
    assert store.state["results"] == {}


@pytest.mark.asyncio
async def test_transaction_failure_does_not_publish_a_command_result() -> None:
    """A failed outer commit must roll back command, events, Effects, and views."""

    store = MemoryStore()
    store.fail_after_yield = True
    service = _service(store)
    authorization = KernelAuthorization.for_user("user-1", OwnerScopeRef.personal("user-1"))

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.submit(_command(), authorization)

    assert store.state["reserved"] == []
    assert store.state["results"] == {}


@pytest.mark.asyncio
async def test_deterministic_rejection_is_persisted_and_reused() -> None:
    """A bad command retry must return one stable rejection without journal events."""

    store = MemoryStore()
    service = _service(store)
    authorization = KernelAuthorization.for_user("user-1", OwnerScopeRef.personal("user-1"))

    first = await service.submit(_command(prompt="   "), authorization)
    second = await service.submit(_command(prompt="   "), authorization)

    assert first.status is CommandResultStatus.REJECTED
    assert first.error_code == "agent_command_rejected"
    assert second == first
    assert store.append_calls == 0
    assert store.state["reserved"] == [COMMAND_ID]


@pytest.mark.asyncio
async def test_command_service_accepts_async_fact_resolution() -> None:
    store = MemoryStore()
    called = False

    async def async_facts(command, state):
        nonlocal called
        called = True
        return _facts(command, state)

    service = CommandService(
        store=store,
        reducers=ReducerRegistry({Workflow.AGENT: agent_reducer}),
        facts_factory=async_facts,
    )

    await service.submit(
        _command(),
        KernelAuthorization.for_user("user-1", OwnerScopeRef.personal("user-1")),
    )

    assert called is True
