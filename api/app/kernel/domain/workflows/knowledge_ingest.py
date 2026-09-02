"""Pure reducer for immutable knowledge candidate construction."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ..commands import CommandEnvelope
from ..decisions import Decision, DecisionFacts, DecisionRejected
from ..effects import EffectDeclaration
from ..events import NewEvent
from ..state import RunState
from ..types import EffectSafety, RunStatus

_NEXT_STAGE = {
    "parse": "chunk",
    "chunk": "embed",
    "embed": "graph",
    "graph": "manifest",
}


class KnowledgeWorkflowError(DecisionRejected):
    """A deterministic knowledge-build command rejection."""

    code = "knowledge_command_rejected"


def _event(
    facts: DecisionFacts,
    index: int,
    type_: str,
    *,
    public: dict[str, Any] | None = None,
    private: dict[str, Any] | None = None,
) -> NewEvent:
    try:
        event_id = facts.event_ids[index]
    except IndexError as exc:
        raise KnowledgeWorkflowError(f"missing event id at decision index {index}") from exc
    return NewEvent(
        event_id=event_id,
        type=type_,
        public_payload=public or {},
        private_payload=private or {},
        occurred_at=facts.now,
    )


def _build_effect(
    facts: DecisionFacts,
    stage: str,
    data: dict[str, Any],
) -> EffectDeclaration:
    try:
        effect_id = facts.effect_ids[0]
    except IndexError as exc:
        raise KnowledgeWorkflowError("knowledge stage requires an Effect id") from exc
    return EffectDeclaration(
        effect_id=effect_id,
        invocation_id=effect_id,
        type="knowledge.build",
        safety=EffectSafety.IDEMPOTENT_WRITE,
        request={
            "stage": stage,
            "knowledge_base_id": data["knowledge_base_id"],
            "candidate_version_id": data["candidate_version_id"],
            "document_ids": list(data.get("document_ids", [])),
            "policy_revision_id": str(facts.policy_revision_id),
        },
        public_summary={"kind": "knowledge_build", "stage": stage},
        max_attempts=3,
    )


def _effect_event(facts: DecisionFacts, index: int, effect: EffectDeclaration) -> NewEvent:
    return _event(
        facts,
        index,
        "EffectRequested",
        public={
            "effect_id": str(effect.effect_id),
            "effect_type": effect.type,
            "safety": effect.safety.value,
            "blocked": False,
            "summary": effect.public_summary,
        },
    )


def _active_effect(state: RunState, payload: dict[str, Any]) -> UUID:
    try:
        effect_id = UUID(str(payload["effect_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise KnowledgeWorkflowError("knowledge outcome requires a valid effect_id") from exc
    if effect_id not in state.active_effect_ids:
        raise KnowledgeWorkflowError("knowledge outcome does not match the active Effect")
    return effect_id


def knowledge_ingest_reducer(
    state: RunState | None,
    command: CommandEnvelope,
    facts: DecisionFacts,
) -> Decision:
    """Decide immutable candidate-build commands."""

    if command.type == "StartKnowledgeIngest":
        if state is not None:
            raise KnowledgeWorkflowError("StartKnowledgeIngest requires a new Run")
        data = {
            "knowledge_base_id": str(command.payload["knowledge_base_id"]),
            "candidate_version_id": str(command.payload["candidate_version_id"]),
            "active_version_id": command.payload.get("active_version_id"),
            "document_ids": [str(value) for value in command.payload.get("document_ids", [])],
            "stage": "parse",
        }
        effect = _build_effect(facts, "parse", data)
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "RunStarted",
                    public={
                        "workflow": "knowledge_ingest",
                        "status": "running",
                        "title": f"Knowledge build {data['knowledge_base_id']}",
                        "data": data,
                    },
                ),
                _event(facts, 1, "KnowledgeCandidateCreated", public=data),
                _effect_event(facts, 2, effect),
            ),
            effects=(effect,),
        )

    if state is None:
        raise KnowledgeWorkflowError(f"{command.type} requires an existing Run")
    if state.status is not RunStatus.RUNNING and command.type != "CancelRun":
        raise KnowledgeWorkflowError("knowledge build is not running")

    if command.type == "EffectSucceeded":
        effect_id = _active_effect(state, command.payload)
        stage = str(state.data.get("stage", ""))
        expected_type = "knowledge.build"
        if command.payload.get("effect_type") != expected_type:
            raise KnowledgeWorkflowError(
                f"expected {expected_type}, got {command.payload.get('effect_type')}"
            )
        succeeded = _event(
            facts,
            0,
            "EffectSucceeded",
            public={"effect_id": str(effect_id), "effect_type": expected_type},
        )
        completed = _event(
            facts,
            1,
            "KnowledgeStageCompleted",
            public={"stage": stage, "next_stage": _NEXT_STAGE.get(stage)},
        )
        if stage == "manifest":
            candidate_id = str(state.data["candidate_version_id"])
            return Decision(
                events=(
                    succeeded,
                    completed,
                    _event(
                        facts,
                        2,
                        "KnowledgeVersionPublished",
                        public={
                            "knowledge_base_id": state.data["knowledge_base_id"],
                            "version_id": candidate_id,
                            "manifest_digest": str(command.payload["manifest_digest"]),
                        },
                    ),
                    _event(facts, 3, "RunCompleted"),
                )
            )
        next_stage = _NEXT_STAGE.get(stage)
        if next_stage is None:
            raise KnowledgeWorkflowError(f"unsupported knowledge stage: {stage}")
        effect = _build_effect(facts, next_stage, state.data)
        return Decision(
            events=(succeeded, completed, _effect_event(facts, 2, effect)),
            effects=(effect,),
        )

    if command.type == "EffectFailed":
        effect_id = _active_effect(state, command.payload)
        code = str(command.payload.get("code", "knowledge_effect_failed"))
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "EffectFailed",
                    public={
                        "effect_id": str(effect_id),
                        "effect_type": str(command.payload.get("effect_type", "")),
                        "code": code,
                    },
                ),
                _event(
                    facts,
                    1,
                    "KnowledgeCandidateFailed",
                    public={
                        "candidate_version_id": state.data["candidate_version_id"],
                        "code": code,
                    },
                ),
                _event(facts, 2, "RunFailed", public={"code": code}),
            )
        )

    if command.type == "CancelRun":
        events = [
            _event(
                facts,
                index,
                "EffectCancelled",
                public={"effect_id": str(effect_id)},
            )
            for index, effect_id in enumerate(state.active_effect_ids)
        ]
        events.extend(
            (
                _event(
                    facts,
                    len(events),
                    "KnowledgeCandidateFailed",
                    public={
                        "candidate_version_id": state.data["candidate_version_id"],
                        "code": "cancelled",
                    },
                ),
                _event(facts, len(events) + 1, "RunCancelled"),
            )
        )
        return Decision(events=tuple(events))

    raise KnowledgeWorkflowError(f"unsupported knowledge command: {command.type}")
