"""Production decision registry for every Run family (D10).

One declarative ``DecisionPlannerSpec`` per Run family replaces the historic
if/elif chain. Each spec declares which activity types its planner may emit so
the kernel can cross-assert planners against the activity registry at startup
instead of discovering an unroutable activity type at runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.execution import activity_types
from app.application.execution.decisions.agent import next_agent_command
from app.application.execution.decisions.ask import next_ask_command
from app.application.execution.decisions.automation import automation_plan
from app.application.execution.decisions.base import next_plan_command
from app.application.execution.decisions.patrol import patrol_plan
from app.application.execution.decisions.resource_build import resource_build_plan
from app.domain.execution.commands import JsonValue, RegisteredCommand
from app.domain.execution.context import RunExecutionContext
from app.domain.execution.run import RunFamily, RunState

PlannerFn = Callable[
    [RunState, RunExecutionContext, Mapping[UUID, dict[str, JsonValue]], datetime],
    RegisteredCommand | None,
]


@dataclass(frozen=True)
class DecisionPlannerSpec:
    """One family's pure planner plus its declared activity-type surface."""

    family: RunFamily
    planner: PlannerFn
    emits_activity_types: frozenset[str]


def _max_retries(context: RunExecutionContext) -> int:
    agent_policy = getattr(context.policy_snapshot.family_policy, "agent", None)
    return agent_policy.max_retries if agent_policy is not None else 0


def _tool_timeout(context: RunExecutionContext) -> int:
    return context.policy_snapshot.common.activity.tool_timeout_seconds


def _plan_agent(
    state: RunState,
    context: RunExecutionContext,
    outcomes: Mapping[UUID, dict[str, JsonValue]],
    now: datetime,
) -> RegisteredCommand | None:
    return next_agent_command(state, context, outcomes=outcomes, now=now)


def _plan_ask(
    state: RunState,
    context: RunExecutionContext,
    outcomes: Mapping[UUID, dict[str, JsonValue]],
    now: datetime,
) -> RegisteredCommand | None:
    # Ask model calls still settle with a decision payload (empty tool_calls +
    # catalog snapshot), so the hydrated outcomes MUST flow through — dropping
    # them trips activity_result's fail-loud digest guard and wedges the Run.
    return next_ask_command(state, context, outcomes=outcomes, now=now)


def _plan_kb_ingest(
    state: RunState,
    context: RunExecutionContext,
    outcomes: Mapping[UUID, dict[str, JsonValue]],
    now: datetime,
) -> RegisteredCommand | None:
    plan = resource_build_plan(
        state.family,
        state.semantic_payload,
        timeout_seconds=_tool_timeout(context),
    )
    return next_plan_command(state, plan, now=now, max_retries=_max_retries(context))


def _plan_automation(
    state: RunState,
    context: RunExecutionContext,
    outcomes: Mapping[UUID, dict[str, JsonValue]],
    now: datetime,
) -> RegisteredCommand | None:
    plan = automation_plan(
        state.semantic_payload,
        timeout_seconds=_tool_timeout(context),
    )
    return next_plan_command(state, plan, now=now, max_retries=_max_retries(context))


def _plan_patrol(
    state: RunState,
    context: RunExecutionContext,
    outcomes: Mapping[UUID, dict[str, JsonValue]],
    now: datetime,
) -> RegisteredCommand | None:
    plan = patrol_plan(
        state.family,
        state.semantic_payload,
        timeout_seconds=_tool_timeout(context),
    )
    return next_plan_command(state, plan, now=now, max_retries=_max_retries(context))


DECISION_PLANNERS: Mapping[RunFamily, DecisionPlannerSpec] = {
    RunFamily.AGENT: DecisionPlannerSpec(
        family=RunFamily.AGENT,
        planner=_plan_agent,
        emits_activity_types=frozenset(
            {
                activity_types.RETRIEVAL_SEARCH,
                activity_types.MODEL_CALL,
                activity_types.TOOL_CALL,
            }
        ),
    ),
    RunFamily.ASK: DecisionPlannerSpec(
        family=RunFamily.ASK,
        planner=_plan_ask,
        emits_activity_types=frozenset(
            {
                activity_types.RETRIEVAL_SEARCH,
                activity_types.MODEL_CALL,
            }
        ),
    ),
    RunFamily.KB_INGEST: DecisionPlannerSpec(
        family=RunFamily.KB_INGEST,
        planner=_plan_kb_ingest,
        emits_activity_types=frozenset({activity_types.KNOWLEDGE_BUILD}),
    ),
    RunFamily.AUTOMATION: DecisionPlannerSpec(
        family=RunFamily.AUTOMATION,
        planner=_plan_automation,
        emits_activity_types=frozenset({activity_types.CHILD_RUN_START}),
    ),
    RunFamily.PATROL: DecisionPlannerSpec(
        family=RunFamily.PATROL,
        planner=_plan_patrol,
        emits_activity_types=frozenset(
            {
                activity_types.PATROL_EXECUTE,
                activity_types.PATROL_VALIDATE,
            }
        ),
    ),
    RunFamily.REMEDIATION: DecisionPlannerSpec(
        family=RunFamily.REMEDIATION,
        planner=_plan_patrol,
        emits_activity_types=frozenset({activity_types.REMEDIATION_EXECUTE}),
    ),
}


def validate_decision_registry(registered_activity_types: Iterable[str]) -> None:
    """Startup cross-assertion between planners and the activity registry.

    Fails fast when a Run family has no planner or a planner declares an
    activity type no handler is admitted for (D10).
    """
    missing_families = set(RunFamily) - set(DECISION_PLANNERS)
    if missing_families:
        raise ValueError(
            "Run families without a registered decision planner: "
            + ", ".join(sorted(family.value for family in missing_families))
        )
    registered = frozenset(registered_activity_types)
    for spec in DECISION_PLANNERS.values():
        unroutable = spec.emits_activity_types - registered
        if unroutable:
            raise ValueError(
                f"decision planner for {spec.family.value} emits activity types "
                f"without an admitted handler: {', '.join(sorted(unroutable))}"
            )


def next_command(
    state: RunState,
    context: RunExecutionContext,
    *,
    outcomes: Mapping[UUID, dict[str, JsonValue]] | None = None,
    now: datetime,
) -> RegisteredCommand | None:
    if state.family is None:
        raise ValueError("Run family is required")
    if state.run_id != context.run_id or state.family != context.family:
        raise ValueError("Run context does not match decision state")
    spec = DECISION_PLANNERS.get(state.family)
    if spec is None:
        raise ValueError(f"unsupported Run family: {state.family}")
    return spec.planner(state, context, outcomes or {}, now)


__all__ = [
    "DECISION_PLANNERS",
    "DecisionPlannerSpec",
    "next_command",
    "validate_decision_registry",
]
