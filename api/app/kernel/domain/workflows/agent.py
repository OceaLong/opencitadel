"""Pure reducer for governed, multi-turn Agent conversations."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from ..commands import CommandEnvelope
from ..decisions import Decision, DecisionFacts, DecisionRejected
from ..effects import EffectDeclaration, TimerDeclaration
from ..events import NewEvent
from ..state import PendingApproval, RunState
from ..types import ApprovalDecision, EffectSafety, RunStatus


class AgentWorkflowError(DecisionRejected):
    """A deterministic Agent command rejection."""

    code = "agent_command_rejected"


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
        raise AgentWorkflowError(f"missing event id at decision index {index}") from exc
    return NewEvent(
        event_id=event_id,
        type=type_,
        public_payload=public or {},
        private_payload=private or {},
        occurred_at=facts.now,
    )


def _model_effect(
    facts: DecisionFacts,
    index: int,
    *,
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    knowledge_version_ids: list[str],
    retrieval_matches: list[dict[str, Any]] | None = None,
) -> EffectDeclaration:
    try:
        effect_id = facts.effect_ids[index]
    except IndexError as exc:
        raise AgentWorkflowError(f"missing Effect id at decision index {index}") from exc
    return EffectDeclaration(
        effect_id=effect_id,
        invocation_id=effect_id,
        type="model.call",
        safety=EffectSafety.READ_ONLY,
        request={
            "prompt": prompt,
            "tool_catalog": tool_catalog,
            "knowledge_version_ids": knowledge_version_ids,
            "retrieval_matches": retrieval_matches or [],
            "policy_revision_id": str(facts.policy_revision_id),
        },
        public_summary={"kind": "model", "knowledge_count": len(knowledge_version_ids)},
        max_attempts=3,
    )


def _retrieval_effect(
    facts: DecisionFacts,
    index: int,
    *,
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    knowledge_version_ids: list[str],
) -> EffectDeclaration:
    try:
        effect_id = facts.effect_ids[index]
    except IndexError as exc:
        raise AgentWorkflowError(f"missing Effect id at decision index {index}") from exc
    return EffectDeclaration(
        effect_id=effect_id,
        invocation_id=effect_id,
        type="knowledge.retrieve",
        safety=EffectSafety.READ_ONLY,
        request={
            "query": prompt,
            "knowledge_version_ids": knowledge_version_ids,
            "continuation": {
                "prompt": prompt,
                "tool_catalog": tool_catalog,
                "knowledge_version_ids": knowledge_version_ids,
            },
            "policy_revision_id": str(facts.policy_revision_id),
        },
        public_summary={"kind": "knowledge_retrieval", "version_count": len(knowledge_version_ids)},
        max_attempts=3,
    )


def _first_turn_effect(
    facts: DecisionFacts,
    *,
    prompt: str,
    tool_catalog: list[dict[str, Any]],
    knowledge_version_ids: list[str],
) -> EffectDeclaration:
    if knowledge_version_ids:
        return _retrieval_effect(
            facts,
            0,
            prompt=prompt,
            tool_catalog=tool_catalog,
            knowledge_version_ids=knowledge_version_ids,
        )
    return _model_effect(
        facts,
        0,
        prompt=prompt,
        tool_catalog=tool_catalog,
        knowledge_version_ids=knowledge_version_ids,
    )


def _effect_requested_event(
    facts: DecisionFacts,
    index: int,
    effect: EffectDeclaration,
) -> NewEvent:
    return _event(
        facts,
        index,
        "EffectRequested",
        public={
            "effect_id": str(effect.effect_id),
            "effect_type": effect.type,
            "safety": effect.safety.value,
            "blocked": effect.requires_approval,
            "approval_id": str(effect.approval_id) if effect.approval_id else None,
            "summary": effect.public_summary,
        },
    )


def _catalog_entry(state: RunState, name: str) -> dict[str, Any]:
    for raw in state.data.get("tool_catalog", []):
        entry = dict(raw)
        if entry.get("name") == name:
            return entry
    raise AgentWorkflowError(f"tool {name!r} is not in the frozen catalog")


def _require_active_effect(state: RunState, payload: dict[str, Any]) -> UUID:
    try:
        effect_id = UUID(str(payload["effect_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentWorkflowError("Effect outcome requires a valid effect_id") from exc
    if effect_id not in state.active_effect_ids:
        raise AgentWorkflowError("Effect outcome does not match an active Effect")
    return effect_id


def _pending_approval(state: RunState, payload: dict[str, Any]) -> PendingApproval:
    try:
        approval_id = UUID(str(payload["approval_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentWorkflowError("approval command requires a valid approval_id") from exc
    for pending in state.pending_approvals:
        if isinstance(pending, dict):
            pending = PendingApproval.model_validate(pending)
        if pending.approval_id == approval_id:
            return pending
    raise AgentWorkflowError("approval is not pending")


def agent_reducer(
    state: RunState | None,
    command: CommandEnvelope,
    facts: DecisionFacts,
) -> Decision:
    """Decide one Agent command without I/O, clocks, or generated identities."""

    if command.type == "StartAgent":
        if state is not None:
            raise AgentWorkflowError("StartAgent requires a new Run")
        prompt = str(command.payload.get("prompt", "")).strip()
        if not prompt:
            raise AgentWorkflowError("prompt must not be blank")
        title = str(command.payload.get("title", "")).strip() or prompt[:80]
        tool_catalog = [dict(entry) for entry in command.payload.get("tool_catalog", [])]
        knowledge_ids = [str(value) for value in command.payload.get("knowledge_version_ids", [])]
        effect = _first_turn_effect(
            facts,
            prompt=prompt,
            tool_catalog=tool_catalog,
            knowledge_version_ids=knowledge_ids,
        )
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "RunStarted",
                    public={
                        "workflow": "agent",
                        "status": "running",
                        "title": title,
                        "data": {
                            "tool_catalog": tool_catalog,
                            "knowledge_version_ids": knowledge_ids,
                        },
                    },
                ),
                _event(
                    facts,
                    1,
                    "PromptAccepted",
                    public={"turn": 1},
                    private={"prompt": prompt},
                ),
                _effect_requested_event(facts, 2, effect),
            ),
            effects=(effect,),
        )

    if state is None:
        raise AgentWorkflowError(f"{command.type} requires an existing Run")

    if command.type == "SubmitPrompt":
        if state.status is not RunStatus.IDLE:
            raise AgentWorkflowError("SubmitPrompt requires an idle Agent Run")
        prompt = str(command.payload.get("prompt", "")).strip()
        if not prompt:
            raise AgentWorkflowError("prompt must not be blank")
        effect = _first_turn_effect(
            facts,
            prompt=prompt,
            tool_catalog=list(state.data.get("tool_catalog", [])),
            knowledge_version_ids=list(state.data.get("knowledge_version_ids", [])),
        )
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "PromptAccepted",
                    public={"turn": state.current_turn + 1},
                    private={"prompt": prompt},
                ),
                _effect_requested_event(facts, 1, effect),
            ),
            effects=(effect,),
        )

    if command.type == "EffectSucceeded":
        effect_id = _require_active_effect(state, command.payload)
        effect_type = str(command.payload.get("effect_type", ""))
        succeeded = _event(
            facts,
            0,
            "EffectSucceeded",
            public={"effect_id": str(effect_id), "effect_type": effect_type},
        )
        if effect_type == "knowledge.retrieve":
            prompt = str(command.payload.get("prompt", "")).strip()
            if not prompt:
                raise AgentWorkflowError("knowledge retrieval continuation lost its prompt")
            model_effect = _model_effect(
                facts,
                0,
                prompt=prompt,
                tool_catalog=list(state.data.get("tool_catalog", [])),
                knowledge_version_ids=list(state.data.get("knowledge_version_ids", [])),
                retrieval_matches=[dict(value) for value in command.payload.get("matches", [])],
            )
            return Decision(
                events=(succeeded, _effect_requested_event(facts, 1, model_effect)),
                effects=(model_effect,),
            )
        if effect_type == "model.call":
            tool_calls = list(command.payload.get("tool_calls") or [])
            if not tool_calls:
                content = str(command.payload.get("content", ""))
                return Decision(
                    events=(
                        succeeded,
                        _event(
                            facts,
                            1,
                            "AssistantMessageCreated",
                            public={"turn": state.current_turn},
                            private={"content": content},
                        ),
                        _event(facts, 2, "TurnCompleted", public={"turn": state.current_turn}),
                    )
                )

            if len(tool_calls) > 1:
                raise AgentWorkflowError("an Agent turn supports at most one tool call")

            events: list[NewEvent] = [succeeded]
            effects: list[EffectDeclaration] = []
            timers: list[TimerDeclaration] = []
            for index, raw_call in enumerate(tool_calls):
                call = dict(raw_call)
                name = str(call.get("name", ""))
                entry = _catalog_entry(state, name)
                try:
                    effect_id_for_tool = facts.effect_ids[index]
                except IndexError as exc:
                    raise AgentWorkflowError(
                        f"missing tool Effect id at decision index {index}"
                    ) from exc
                requires_approval = bool(entry.get("requires_approval", False))
                approval_id = None
                if requires_approval:
                    try:
                        approval_id = facts.approval_ids[index]
                    except IndexError as exc:
                        raise AgentWorkflowError(
                            f"missing approval id at decision index {index}"
                        ) from exc
                declared_effect_type = str(entry.get("effect_type") or "tool.call")
                if declared_effect_type not in {"tool.call", "file.operation"}:
                    raise AgentWorkflowError("tool catalog declares an unsupported Effect type")
                effect_request = (
                    {
                        "operation": name.removeprefix("file."),
                        **dict(call.get("arguments") or {}),
                        "capability": entry,
                        "policy_revision_id": str(facts.policy_revision_id),
                    }
                    if declared_effect_type == "file.operation"
                    else {
                        "name": name,
                        "arguments": dict(call.get("arguments") or {}),
                        "capability": entry,
                        "policy_revision_id": str(facts.policy_revision_id),
                    }
                )
                tool_effect = EffectDeclaration(
                    effect_id=effect_id_for_tool,
                    invocation_id=effect_id_for_tool,
                    type=declared_effect_type,
                    safety=EffectSafety(
                        entry.get("safety", EffectSafety.NON_IDEMPOTENT_WRITE.value)
                    ),
                    request=effect_request,
                    public_summary={"kind": "tool", "name": name},
                    requires_approval=requires_approval,
                    approval_id=approval_id,
                    reviewer_user_ids=facts.reviewer_user_ids if requires_approval else (),
                )
                if requires_approval:
                    try:
                        timer_id = facts.timer_ids[index]
                    except IndexError as exc:
                        raise AgentWorkflowError(
                            f"missing approval timer id at decision index {index}"
                        ) from exc
                    expires_at = facts.now + timedelta(seconds=facts.approval_ttl_seconds)
                    events.append(
                        _event(
                            facts,
                            len(events),
                            "ApprovalRequested",
                            public={
                                "approval_id": str(approval_id),
                                "effect_id": str(effect_id_for_tool),
                                "timer_id": str(timer_id),
                                "subject": name,
                                "risk_summary": {
                                    "safety": tool_effect.safety.value,
                                    "tool": name,
                                },
                                "reviewer_user_ids": list(facts.reviewer_user_ids),
                                "expires_at": expires_at.isoformat(),
                            },
                        )
                    )
                    timers.append(
                        TimerDeclaration(
                            timer_id=timer_id,
                            due_at=expires_at,
                            command_type="ExpireApproval",
                            command_payload={"approval_id": str(approval_id)},
                        )
                    )
                events.append(_effect_requested_event(facts, len(events), tool_effect))
                effects.append(tool_effect)
            if any(effect.requires_approval for effect in effects):
                events.append(
                    _event(facts, len(events), "RunWaiting", public={"reason": "approval"})
                )
            return Decision(events=tuple(events), effects=tuple(effects), timers=tuple(timers))

        if effect_type in {"tool.call", "file.operation"}:
            try:
                next_effect_id = facts.effect_ids[0]
            except IndexError as exc:
                raise AgentWorkflowError("missing model Effect id after tool result") from exc
            result = command.payload.get("result")
            model_effect = _model_effect(
                facts,
                0,
                prompt="Continue from the recorded tool result.",
                tool_catalog=list(state.data.get("tool_catalog", [])),
                knowledge_version_ids=list(state.data.get("knowledge_version_ids", [])),
            ).model_copy(
                update={
                    "effect_id": next_effect_id,
                    "invocation_id": next_effect_id,
                    "request": {
                        "tool_result": result,
                        "tool_catalog": list(state.data.get("tool_catalog", [])),
                        "knowledge_version_ids": list(state.data.get("knowledge_version_ids", [])),
                        "policy_revision_id": str(facts.policy_revision_id),
                    },
                }
            )
            return Decision(
                events=(
                    succeeded,
                    _event(
                        facts,
                        1,
                        "ToolResultRecorded",
                        public={"effect_id": str(effect_id)},
                        private={"result": result},
                    ),
                    _effect_requested_event(facts, 2, model_effect),
                ),
                effects=(model_effect,),
            )
        raise AgentWorkflowError(f"unsupported Agent Effect type: {effect_type}")

    if command.type == "DecideApproval":
        pending = _pending_approval(state, command.payload)
        if command.actor_user_id not in pending.reviewer_user_ids:
            raise AgentWorkflowError("actor is not a frozen approval reviewer")
        try:
            decision = ApprovalDecision(str(command.payload["decision"]))
        except (KeyError, ValueError) as exc:
            raise AgentWorkflowError("approval decision must be approved or rejected") from exc
        decided = _event(
            facts,
            0,
            "ApprovalDecided",
            public={
                "approval_id": str(pending.approval_id),
                "effect_id": str(pending.effect_id),
                "timer_id": str(pending.timer_id),
                "decision": decision.value,
                "decided_by_user_id": command.actor_user_id,
                "feedback": str(command.payload.get("feedback", "")),
            },
        )
        if decision is ApprovalDecision.APPROVED:
            return Decision(
                events=(
                    decided,
                    _event(
                        facts,
                        1,
                        "EffectReleased",
                        public={"effect_id": str(pending.effect_id)},
                    ),
                    _event(facts, 2, "RunResumed"),
                )
            )
        return Decision(
            events=(
                decided,
                _event(
                    facts,
                    1,
                    "EffectCancelled",
                    public={"effect_id": str(pending.effect_id)},
                ),
                _event(facts, 2, "TurnCompleted", public={"reason": "approval_rejected"}),
            )
        )

    if command.type == "ExpireApproval":
        pending = _pending_approval(state, command.payload)
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "ApprovalExpired",
                    public={
                        "approval_id": str(pending.approval_id),
                        "effect_id": str(pending.effect_id),
                        "timer_id": str(pending.timer_id),
                    },
                ),
                _event(
                    facts,
                    1,
                    "EffectCancelled",
                    public={"effect_id": str(pending.effect_id)},
                ),
                _event(facts, 2, "TurnCompleted", public={"reason": "approval_expired"}),
            )
        )

    if command.type == "EffectFailed":
        effect_id = _require_active_effect(state, command.payload)
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "EffectFailed",
                    public={
                        "effect_id": str(effect_id),
                        "effect_type": str(command.payload.get("effect_type", "")),
                        "code": str(command.payload.get("code", "effect_failed")),
                    },
                ),
                _event(
                    facts,
                    1,
                    "RunFailed",
                    public={"code": str(command.payload.get("code", "effect_failed"))},
                ),
            )
        )

    if command.type == "EffectOutcomeUnknown":
        effect_id = _require_active_effect(state, command.payload)
        return Decision(
            events=(
                _event(
                    facts,
                    0,
                    "EffectOutcomeUnknown",
                    public={
                        "effect_id": str(effect_id),
                        "effect_type": str(command.payload.get("effect_type", "")),
                        "code": "effect_outcome_unknown",
                    },
                ),
                _event(
                    facts,
                    1,
                    "RunFailed",
                    public={"code": "effect_outcome_unknown"},
                ),
            )
        )

    if command.type == "CancelRun":
        if state.status in {RunStatus.CANCELLED, RunStatus.ARCHIVED}:
            return Decision()
        events = [
            _event(
                facts,
                index,
                "EffectCancelled",
                public={"effect_id": str(effect_id)},
            )
            for index, effect_id in enumerate(state.active_effect_ids)
        ]
        if state.status is RunStatus.WAITING:
            events.append(_event(facts, len(events), "ApprovalCancelled"))
        events.append(
            _event(
                facts,
                len(events),
                "RunCancelled",
                public={"reason": str(command.payload.get("reason", "cancelled"))},
            )
        )
        return Decision(events=tuple(events))

    raise AgentWorkflowError(f"unsupported Agent command: {command.type}")
