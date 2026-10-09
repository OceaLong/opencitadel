"""Persistence helpers for execution playback checkpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select

from app.application.execution.playback import reduce_facts
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.infrastructure.execution.original_evidence import OriginalEvidence
from app.infrastructure.models.execution_view import (
    ExecutionPlaybackCheckpointORM,
    ExecutionRunViewORM,
    ExecutionViewObservationORM,
)

CHECKPOINT_FORMAL_INTERVAL = 500
CHECKPOINT_STATE_SCHEMA_VERSION = 1
_TERMINAL = {"completed", "failed", "cancelled"}


class PlaybackUnavailable(ValueError):
    code = "playback_unavailable"
    recoverable = True


def _scope_key(scope: OwnerScope) -> str:
    if scope.type == OwnerScopeType.TEAM:
        if not scope.team_id:
            raise PlaybackUnavailable("playback trusted scope is invalid")
        return f"team:{scope.team_id}"
    return f"user:{scope.user_id}"


@dataclass(frozen=True)
class PlaybackSnapshot:
    state: dict
    missing_intervals: tuple[dict, ...]


def _facts(rows) -> list[dict]:
    result = []
    for row in rows:
        result.extend(
            {
                **fact,
                "position": (row.formal_position, row.progress_position),
                "observed_order": row.observed_order,
            }
            for fact in row.public_payload.get("facts", [])
        )
    return result


def _missing_intervals(run: ExecutionRunViewORM) -> tuple[dict, ...]:
    completeness = run.completeness or {}
    return tuple(dict(item) for item in completeness.get("missing_intervals", []))


def _target_relevant(intervals, boundary: PlaybackBoundary) -> tuple[dict, ...]:
    result = []
    for source in intervals:
        item = dict(source)
        start = item.get("start")
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if start is not None and start > boundary.observed_at:
            continue
        result.append(item)
    return tuple(result)


def _merge_intervals(*groups) -> tuple[dict, ...]:
    unique = {}
    for group in groups:
        for item in group:
            marker = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
            unique[marker] = dict(item)
    return tuple(unique[key] for key in sorted(unique))


def _journal_gaps(observed_orders, target_order: int) -> tuple[dict, ...]:
    present = set(observed_orders)
    missing = sorted(set(range(1, target_order + 1)) - present)
    if not missing:
        return ()
    intervals = []
    start = previous = missing[0]
    for order in missing[1:]:
        if order != previous + 1:
            intervals.append(
                {
                    "start": None,
                    "end": None,
                    "reason": "journal_observation_gap",
                    "start_order": start,
                    "end_order": previous,
                }
            )
            start = order
        previous = order
    intervals.append(
        {
            "start": None,
            "end": None,
            "reason": "journal_observation_gap",
            "start_order": start,
            "end_order": previous,
        }
    )
    return tuple(intervals)


async def validate_playback_boundary(
    session,
    boundary: PlaybackBoundary,
    *,
    trusted_scope: OwnerScope,
    evidence: OriginalEvidence | None = None,
) -> None:
    """Fail closed unless the signed boundary is an exact scoped journal cut."""
    scope_key = _scope_key(trusted_scope)
    identity = {
        "run_id": boundary.run_id,
        "scope_key": scope_key,
        "projector_version": boundary.projector_version,
        "observed_order": boundary.observed_order,
    }
    try:
        row = (
            await session.execute(
                select(
                    ExecutionViewObservationORM.formal_position,
                    ExecutionViewObservationORM.progress_position,
                    ExecutionViewObservationORM.projection_revision,
                    ExecutionViewObservationORM.observed_at,
                ).where(
                    ExecutionViewObservationORM.run_id == boundary.run_id,
                    ExecutionViewObservationORM.scope_key == scope_key,
                    ExecutionViewObservationORM.projector_version == boundary.projector_version,
                    ExecutionViewObservationORM.observed_order == boundary.observed_order,
                )
            )
        ).one_or_none()
    except Exception as error:
        if evidence is not None:
            evidence.retain(
                "playback-boundary-observation",
                {"identity": identity, "row": None, "error": type(error).__name__},
            )
        raise
    if evidence is not None:
        evidence.retain(
            "playback-boundary-observation", {"identity": identity, "row": row, "error": None}
        )
    check_playback_boundary(row, boundary)


def check_playback_boundary(row, boundary):
    """Shared comparison of the actual selected row, including explicit absence."""
    if row is None:
        raise PlaybackUnavailable("playback boundary is unavailable in trusted scope")
    if (
        row.formal_position != boundary.formal_position
        or row.progress_position != boundary.progress_position
        or row.projection_revision != boundary.projection_revision
        or row.observed_at != boundary.observed_at
    ):
        raise PlaybackUnavailable("playback boundary does not match persisted journal cut")


async def maybe_write_checkpoint(session, *, run, observation) -> None:
    """Persist a checkpoint at the current locked Run cut when required."""
    if observation.source_kind != "formal":
        return
    formal_count = await session.scalar(
        select(func.count())
        .select_from(ExecutionViewObservationORM)
        .where(
            ExecutionViewObservationORM.run_id == run.run_id,
            ExecutionViewObservationORM.source_kind == "formal",
            ExecutionViewObservationORM.projector_version == observation.projector_version,
        )
    )
    terminal_fact = any(
        fact.get("kind") == "run" and fact.get("patch", {}).get("status") in _TERMINAL
        for fact in observation.public_payload.get("facts", [])
    )
    if formal_count % CHECKPOINT_FORMAL_INTERVAL and not terminal_fact:
        return
    rows = (
        await session.scalars(
            select(ExecutionViewObservationORM)
            .where(
                ExecutionViewObservationORM.run_id == run.run_id,
                ExecutionViewObservationORM.projector_version == observation.projector_version,
                ExecutionViewObservationORM.observed_order <= observation.observed_order,
            )
            .order_by(ExecutionViewObservationORM.observed_order)
        )
    ).all()
    boundary = PlaybackBoundary(
        run_id=run.run_id,
        formal_position=observation.formal_position,
        progress_position=observation.progress_position,
        observed_order=observation.observed_order,
        projection_revision=observation.projection_revision,
        observed_at=observation.observed_at,
        projector_version=observation.projector_version,
    )
    state = reduce_facts(_facts(rows), boundary)
    session.add(
        ExecutionPlaybackCheckpointORM(
            id=uuid5(
                NAMESPACE_URL,
                f"opencitadel:view-checkpoint:{run.run_id}:{observation.projector_version}:{observation.observed_order}",
            ),
            run_id=run.run_id,
            boundary=observation.observed_order,
            projector_version=observation.projector_version,
            formal_position=observation.formal_position,
            progress_position=observation.progress_position,
            observed_order=observation.observed_order,
            projection_revision=observation.projection_revision,
            observed_at=observation.observed_at,
            state_ref={
                "schema_version": CHECKPOINT_STATE_SCHEMA_VERSION,
                "projector_version": observation.projector_version,
                "state": state,
                "missing_intervals": list(_missing_intervals(run)),
            },
            owner_user_id=run.owner_user_id,
            team_id=run.team_id,
            created_by="execution-projector",
        )
    )
    await session.flush()


async def load_playback(
    session,
    boundary: PlaybackBoundary,
    *,
    trusted_scope: OwnerScope,
    use_checkpoint: bool = True,
    evidence: OriginalEvidence | None = None,
) -> PlaybackSnapshot:
    """Restore a target cut from the latest compatible checkpoint and suffix."""
    if evidence is not None:
        evidence.retain("playback-boundary", boundary)
        evidence.reserve_state(boundary.observed_order, bytes_per_item=256)
    scope_key = _scope_key(trusted_scope)
    await validate_playback_boundary(
        session, boundary, trusted_scope=trusted_scope, evidence=evidence
    )
    run = await session.scalar(
        select(ExecutionRunViewORM).where(
            ExecutionRunViewORM.run_id == boundary.run_id,
            ExecutionRunViewORM.scope_key == scope_key,
        )
    )
    if run is None or run.projector_version != boundary.projector_version:
        raise PlaybackUnavailable("playback source journal version is unavailable in trusted scope")
    checkpoint = None
    checkpoint_filters = (
        ExecutionPlaybackCheckpointORM.run_id == boundary.run_id,
        ExecutionPlaybackCheckpointORM.scope_key == scope_key,
        ExecutionPlaybackCheckpointORM.projector_version == boundary.projector_version,
        ExecutionPlaybackCheckpointORM.observed_order <= boundary.observed_order,
        ExecutionPlaybackCheckpointORM.formal_position <= boundary.formal_position,
        ExecutionPlaybackCheckpointORM.progress_position <= boundary.progress_position,
    )
    if use_checkpoint:
        checkpoint = await session.scalar(
            select(ExecutionPlaybackCheckpointORM)
            .where(*checkpoint_filters)
            .order_by(ExecutionPlaybackCheckpointORM.observed_order.desc())
            .limit(1)
        )
    checkpoint_missing = (
        await session.scalars(
            select(ExecutionPlaybackCheckpointORM.state_ref["missing_intervals"]).where(
                *checkpoint_filters
            )
        )
    ).all()
    if evidence is not None:
        evidence.retain("playback-run", run)
        evidence.retain("playback-checkpoint", checkpoint)
        evidence.retain("playback-missing", checkpoint_missing)
        evidence.reserve_state(sum(len(items or []) for items in checkpoint_missing))
    observed_orders = (
        await session.scalars(
            select(ExecutionViewObservationORM.observed_order)
            .where(
                ExecutionViewObservationORM.run_id == boundary.run_id,
                ExecutionViewObservationORM.scope_key == scope_key,
                ExecutionViewObservationORM.projector_version == boundary.projector_version,
                ExecutionViewObservationORM.observed_order <= boundary.observed_order,
                ExecutionViewObservationORM.formal_position <= boundary.formal_position,
                ExecutionViewObservationORM.progress_position <= boundary.progress_position,
            )
            .order_by(ExecutionViewObservationORM.observed_order)
        )
    ).all()
    if evidence is not None:
        evidence.retain("playback-orders", observed_orders)
    prefix = playback_prefix(boundary, run, checkpoint, checkpoint_missing, observed_orders)
    after_order = prefix[1]
    rows = (
        await session.scalars(
            select(ExecutionViewObservationORM)
            .where(
                ExecutionViewObservationORM.run_id == boundary.run_id,
                ExecutionViewObservationORM.scope_key == scope_key,
                ExecutionViewObservationORM.projector_version == boundary.projector_version,
                ExecutionViewObservationORM.observed_order > after_order,
                ExecutionViewObservationORM.observed_order <= boundary.observed_order,
                ExecutionViewObservationORM.formal_position <= boundary.formal_position,
                ExecutionViewObservationORM.progress_position <= boundary.progress_position,
            )
            .order_by(ExecutionViewObservationORM.observed_order)
        )
    ).all()
    if evidence is not None:
        evidence.retain("playback-observations", rows)
        evidence.reserve_state(
            sum(len(row.public_payload.get("facts", [])) for row in rows), bytes_per_item=1024
        )
    return finish_playback(boundary, prefix, rows)


def playback_prefix(boundary, run, checkpoint, checkpoint_missing, observed_orders):
    """Shared checkpoint/gap semantics after original typed input acquisition."""
    initial_state = None
    after_order = 0
    missing = _target_relevant(_missing_intervals(run), boundary)
    historical_missing = []
    for intervals in checkpoint_missing:
        historical_missing.extend(intervals or [])
    missing = _merge_intervals(
        _target_relevant(historical_missing, boundary),
        missing,
    )
    if checkpoint is not None:
        ref = checkpoint.state_ref
        if (
            ref.get("schema_version") != CHECKPOINT_STATE_SCHEMA_VERSION
            or ref.get("projector_version") != boundary.projector_version
        ):
            raise PlaybackUnavailable("unsupported playback checkpoint state")
        initial_state = ref["state"]
        after_order = checkpoint.observed_order
    journal_gaps = _journal_gaps(observed_orders, boundary.observed_order)
    missing = _merge_intervals(missing, journal_gaps)
    prefix_unverifiable = checkpoint is not None and any(
        item["start_order"] <= checkpoint.observed_order for item in journal_gaps
    )
    if prefix_unverifiable:
        # A checkpoint can no longer prove state contributed by a subsequently
        # unavailable prefix observation. Fold the remaining journal exactly
        # as full replay does and expose the gap instead of filling history
        # from the checkpoint's now-unverifiable materialized state.
        initial_state = None
        after_order = 0
    return initial_state, after_order, missing


def finish_playback(boundary, prefix, rows):
    initial_state, _after_order, missing = prefix
    state = reduce_facts(_facts(rows), boundary, initial_state=initial_state)
    return PlaybackSnapshot(state=state, missing_intervals=missing)


__all__ = [
    "CHECKPOINT_FORMAL_INTERVAL",
    "PlaybackSnapshot",
    "PlaybackUnavailable",
    "load_playback",
    "maybe_write_checkpoint",
    "validate_playback_boundary",
]
