"""Knowledge ingestion workflow tests for immutable candidate publication."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.state import RunState, apply_event
from app.kernel.domain.types import OwnerScopeRef, RunStatus, Workflow
from app.kernel.domain.workflows.knowledge_ingest import knowledge_ingest_reducer

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID(int=1101)


def _command(type_: str, payload: dict[str, object], version: int | None = None):
    return CommandEnvelope(
        command_id=UUID(int=1200 + (version or 0)),
        run_id=RUN_ID,
        workflow=Workflow.KNOWLEDGE_INGEST,
        type=type_,
        payload=payload,
        expected_stream_version=version,
        owner_scope=OwnerScopeRef.team("team-1"),
        actor_user_id="owner-1",
        request_id="request-kb",
        submitted_at=NOW,
    )


def _facts(effect_id: UUID | None = None):
    return DecisionFacts(
        now=NOW,
        actor_user_id="owner-1",
        request_id="request-kb",
        policy_revision_id=UUID(int=1300),
        event_ids=tuple(UUID(int=1400 + index) for index in range(8)),
        effect_ids=(effect_id,) if effect_id else (),
    )


def _state(*, stage: str, effect_id: UUID) -> RunState:
    return RunState(
        run_id=RUN_ID,
        workflow=Workflow.KNOWLEDGE_INGEST,
        owner_scope=OwnerScopeRef.team("team-1"),
        status=RunStatus.RUNNING,
        stream_version=3,
        stream_hash="b" * 64,
        active_effect_ids=(effect_id,),
        data={
            "knowledge_base_id": "kb-1",
            "candidate_version_id": "candidate-v2",
            "active_version_id": "published-v1",
            "stage": stage,
        },
    )


def test_start_creates_a_candidate_before_parse_effect() -> None:
    """Ingestion must never write into the currently published version."""

    effect_id = UUID(int=1501)
    decision = knowledge_ingest_reducer(
        None,
        _command(
            "StartKnowledgeIngest",
            {
                "knowledge_base_id": "kb-1",
                "candidate_version_id": "candidate-v2",
                "active_version_id": "published-v1",
                "document_ids": ["doc-1"],
            },
            version=0,
        ),
        _facts(effect_id),
    )

    assert tuple(event.type for event in decision.events) == (
        "RunStarted",
        "KnowledgeCandidateCreated",
        "EffectRequested",
    )
    assert decision.effects[0].type == "knowledge.build"
    assert decision.effects[0].request["candidate_version_id"] == "candidate-v2"


def test_successful_stages_advance_in_a_closed_sequence() -> None:
    """Skipping a required build stage must be impossible."""

    stages = ("parse", "chunk", "embed", "graph")
    next_types = ("knowledge.build",) * 4
    for index, (stage, next_type) in enumerate(zip(stages, next_types, strict=True), start=1):
        current = UUID(int=1600 + index)
        following = UUID(int=1700 + index)
        decision = knowledge_ingest_reducer(
            _state(stage=stage, effect_id=current),
            _command(
                "EffectSucceeded",
                {"effect_id": str(current), "effect_type": "knowledge.build"},
                version=3,
            ),
            _facts(following),
        )
        assert tuple(event.type for event in decision.events) == (
            "EffectSucceeded",
            "KnowledgeStageCompleted",
            "EffectRequested",
        )
        assert decision.effects[0].type == next_type


def test_manifest_success_publishes_candidate_and_completes_run() -> None:
    """Only a complete manifest may switch the active version."""

    current = UUID(int=1800)
    decision = knowledge_ingest_reducer(
        _state(stage="manifest", effect_id=current),
        _command(
            "EffectSucceeded",
            {
                "effect_id": str(current),
                "effect_type": "knowledge.build",
                "manifest_digest": "d" * 64,
            },
            version=3,
        ),
        _facts(),
    )

    assert tuple(event.type for event in decision.events) == (
        "EffectSucceeded",
        "KnowledgeStageCompleted",
        "KnowledgeVersionPublished",
        "RunCompleted",
    )


def test_failed_candidate_never_changes_active_version() -> None:
    """Failure must leave the previously published knowledge authority intact."""

    current = UUID(int=1900)
    state = _state(stage="embed", effect_id=current)
    decision = knowledge_ingest_reducer(
        state,
        _command(
            "EffectFailed",
            {"effect_id": str(current), "effect_type": "knowledge.build", "code": "timeout"},
            version=3,
        ),
        _facts(),
    )
    replayed = state
    for version, event in enumerate(decision.events, start=4):
        stored = type("Event", (), {})()
        stored.run_id = RUN_ID
        stored.owner_scope = state.owner_scope
        stored.type = event.type
        stored.public_payload = event.public_payload
        stored.version = version
        stored.hash = f"{version:064x}"
        stored.previous_hash = replayed.stream_hash
        replayed = apply_event(replayed, stored)

    assert tuple(event.type for event in decision.events) == (
        "EffectFailed",
        "KnowledgeCandidateFailed",
        "RunFailed",
    )
    assert replayed.data["active_version_id"] == "published-v1"
    assert replayed.status is RunStatus.FAILED
