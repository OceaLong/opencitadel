"""Generic reducer gates shared by every workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import Decision, DecisionFacts
from app.kernel.domain.reducer import (
    CommandVersionConflict,
    OwnerScopeConflict,
    ReducerRegistry,
    UnknownWorkflow,
)
from app.kernel.domain.state import RunState
from app.kernel.domain.types import OwnerScopeRef, RunStatus, Workflow

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID("00000000-0000-0000-0000-000000000101")
COMMAND_ID = UUID("00000000-0000-0000-0000-000000000301")


def _command(
    *,
    workflow: Workflow = Workflow.AGENT,
    scope: OwnerScopeRef | None = None,
    expected_version: int | None = 3,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=COMMAND_ID,
        run_id=RUN_ID,
        workflow=workflow,
        type="SubmitPrompt",
        schema_version=1,
        payload={"prompt": "inspect the repository"},
        expected_stream_version=expected_version,
        owner_scope=scope or OwnerScopeRef.personal("user-1"),
        actor_user_id="user-1",
        request_id="request-1",
        submitted_at=NOW,
    )


def _state() -> RunState:
    return RunState(
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        owner_scope=OwnerScopeRef.personal("user-1"),
        status=RunStatus.IDLE,
        stream_version=3,
        stream_hash="a" * 64,
    )


def _facts() -> DecisionFacts:
    return DecisionFacts(
        now=NOW,
        actor_user_id="user-1",
        request_id="request-1",
        policy_revision_id=UUID("00000000-0000-0000-0000-000000000401"),
        event_ids=(UUID(int=9501), UUID(int=9502), UUID(int=9503)),
    )


def test_registry_dispatches_to_the_registered_workflow() -> None:
    """A command for a known workflow must reach exactly that reducer."""

    calls: list[str] = []

    def decide(state, command, facts):
        calls.append(command.type)
        return Decision()

    registry = ReducerRegistry({Workflow.AGENT: decide})

    assert registry.decide(_state(), _command(), _facts()) == Decision()
    assert calls == ["SubmitPrompt"]


def test_registry_rejects_an_owner_scope_change() -> None:
    """A command cannot move an existing stream into another tenant."""

    registry = ReducerRegistry({Workflow.AGENT: lambda *_: Decision()})

    with pytest.raises(OwnerScopeConflict):
        registry.decide(
            _state(),
            _command(scope=OwnerScopeRef.personal("user-2")),
            _facts(),
        )


def test_registry_rejects_a_stale_expected_version() -> None:
    """A stale client must not silently decide from an old stream head."""

    registry = ReducerRegistry({Workflow.AGENT: lambda *_: Decision()})

    with pytest.raises(CommandVersionConflict, match="expected 2, current 3"):
        registry.decide(_state(), _command(expected_version=2), _facts())


def test_registry_rejects_an_unregistered_workflow() -> None:
    """Removing a workflow must make its commands fail closed."""

    registry = ReducerRegistry({})

    with pytest.raises(UnknownWorkflow, match="agent"):
        registry.decide(_state(), _command(), _facts())


def test_archive_restore_and_purge_are_universal_journal_transitions() -> None:
    registry = ReducerRegistry({Workflow.AGENT: lambda *_: Decision()})
    archive = _command().model_copy(
        update={
            "type": "ArchiveRun",
            "payload": {"purge_after": "2026-10-02T08:00:00+00:00"},
        }
    )
    archived = registry.decide(_state(), archive, _facts())
    assert [event.type for event in archived.events] == ["RunArchived"]

    archived_state = _state().model_copy(
        update={
            "status": RunStatus.ARCHIVED,
            "purge_after": datetime(2026, 10, 2, 8, 0, tzinfo=UTC),
        }
    )
    restored = registry.decide(
        archived_state,
        archive.model_copy(update={"type": "RestoreRun", "payload": {}}),
        _facts(),
    )
    purged = registry.decide(
        archived_state,
        archive.model_copy(update={"type": "PurgeRun", "payload": {}}),
        _facts(),
    )
    assert [event.type for event in restored.events] == ["RunRestored"]
    assert [event.type for event in purged.events] == ["RunPurged"]
