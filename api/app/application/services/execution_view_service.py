"""Typed execution queries; one selected journal cut for every public entity."""

import base64
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.dto.execution_view import (
    ApprovalView,
    ArtifactReference,
    Completeness,
    MessageView,
    RunView,
    RunViewPage,
    StepView,
    StepViewPage,
    TimelineBucket,
    TimelineKeyEvent,
    TimelineView,
    ViewPage,
    ViewScope,
)
from app.application.ports.execution_view import (
    ExecutionViewPort,
    ViewCursorInvalid,
    ViewNotFound,
    ViewRevisionExpired,
)
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope


def _duration(start, end, boundary):
    if start is None:
        return None
    start = datetime.fromisoformat(start) if isinstance(start, str) else start
    end = datetime.fromisoformat(end) if isinstance(end, str) else end
    return max(0, int(((min(end, boundary) if end else boundary) - start).total_seconds() * 1000))


def assemble_view(*, scope, boundary, state, latest_available, missing_intervals):
    raw = dict(state.get("run", {}).get(str(boundary.run_id), {}))
    if not raw.get("family") or not raw.get("status"):
        raise ViewRevisionExpired("run state is not reconstructable at this boundary")
    completeness = dict(
        raw.get("completeness")
        or {
            "state": "partial",
            "missing_fields": ["configuration", "purpose"],
            "missing_intervals": [],
        }
    )
    intervals = [*completeness.get("missing_intervals", []), *missing_intervals]
    completeness["missing_intervals"] = list({str(i): i for i in intervals}.values())
    if intervals:
        completeness["state"] = "partial"
    run = RunView(
        **{
            "family": "unknown",
            "status": "unknown",
            "wait_reason": None,
            "source": None,
            "purpose": "unknown",
            "capabilities": [],
            **raw,
            "run_id": boundary.run_id,
            "projection_revision": boundary.projection_revision,
            "scope": ViewScope(
                owner_user_id=scope.user_id if scope.team_id is None else None,
                team_id=scope.team_id,
            ),
            "as_of": boundary.observed_at,
            "latest_available": latest_available,
            "completeness": completeness,
            "duration_ms": _duration(
                raw.get("admitted_at"), raw.get("terminal_at"), boundary.observed_at
            ),
        }
    )
    steps = [
        assemble_step(boundary, identity, raw_step)
        for identity, raw_step in state.get("step", {}).items()
    ]
    return ViewPage(
        run=run,
        steps=steps,
        next_cursor=None,
        revision=boundary.projection_revision,
        approvals=[
            ApprovalView(**{"approval_id": k, **v}) for k, v in state.get("approval", {}).items()
        ],
        artifacts=[
            ArtifactReference(**{"artifact_id": k, **v})
            for k, v in state.get("artifact", {}).items()
        ],
        messages=[MessageView(message_id=k, **v) for k, v in state.get("message", {}).items()],
    )


def assemble_step(boundary, identity, raw_step):
    fields = {k: v for k, v in raw_step.items() if k in StepView.model_fields}
    return StepView(
        **{
            "kind": "unknown",
            "status": "unknown",
            "completeness": Completeness(state="partial", missing_fields=[], missing_intervals=[]),
            **fields,
            "step_id": identity,
            "run_id": boundary.run_id,
            "projection_revision": boundary.projection_revision,
            "duration_ms": _duration(
                fields.get("started_at"), fields.get("ended_at"), boundary.observed_at
            ),
        }
    )


def _key(scope):
    return f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"


def _filters(filters, kind):
    aliases = {"parent": "parent_step_id", "state": "status"} if kind == "steps" else {}
    result = {aliases.get(k, k): v for k, v in (filters or {}).items() if v is not None}
    allowed = (
        {
            "family",
            "state",
            "mode",
            "configuration",
            "start",
            "end",
            "purpose",
            "source_entity_type",
            "source_entity_id",
        }
        if kind == "runs"
        else {"kind", "status", "parent_step_id", "tool_name", "activity_id", "attempt_id"}
    )
    if set(result) - allowed:
        raise ViewCursorInvalid("unsupported filters")
    if ("source_entity_type" in result) != ("source_entity_id" in result):
        raise ViewCursorInvalid("source identity requires type and id")
    from app.domain.execution.family import RunFamily
    from app.domain.execution.run import RunStatus

    enums = {
        "family": {x.value for x in RunFamily},
        "state": {x.value for x in RunStatus},
        "mode": {"production", "recorded", "isolated", "unknown"},
        "purpose": {"production", "evaluation_subject", "evaluation_judge", "unknown"},
        "kind": {"model", "tool", "activity", "phase", "approval", "clarification", "unknown"},
        "status": {
            "new",
            "queued",
            "running",
            "waiting",
            "completed",
            "failed",
            "cancelled",
            "deferred",
            "unknown",
        },
    }
    for k, v in result.items():
        if k in enums and v not in enums[k]:
            raise ViewCursorInvalid(f"unsupported {k} value")
        if k in ("start", "end"):
            try:
                dt = datetime.fromisoformat(v) if isinstance(v, str) else v
                if dt.utcoffset() is None:
                    raise ValueError
                result[k] = dt.isoformat()
            except (ValueError, TypeError, AttributeError) as error:
                raise ViewCursorInvalid("filter time must be timezone aware") from error
        elif not isinstance(v, str) or not v:
            raise ViewCursorInvalid("filter must be a nonempty string")
    if (
        "start" in result
        and "end" in result
        and datetime.fromisoformat(result["start"]) > datetime.fromisoformat(result["end"])
    ):
        raise ViewCursorInvalid("time range is reversed")
    return result


def _limit(value, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ViewCursorInvalid(f"limit must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class StepCut:
    """Authorized exact Step and immutable formal cut, with F04's own cursor."""

    step: StepView
    boundary: PlaybackBoundary
    at: str


class ExecutionViewService:
    def __init__(self, port: ExecutionViewPort, *, cursor_secret: bytes):
        if len(cursor_secret) < 16:
            raise ValueError("cursor secret must contain at least 16 bytes")
        self.port = port
        self.cursor_secret = cursor_secret

    def _encode(self, scope, kind, **payload):
        raw = json.dumps(
            {"v": 1, "scope": _key(scope), "kind": kind, **payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return (
            base64.urlsafe_b64encode(raw + hmac.digest(self.cursor_secret, raw, "sha256"))
            .decode()
            .rstrip("=")
        )

    def _decode(self, token, scope, kind, **expected):
        try:
            if not isinstance(token, str) or len(token) > 16384:
                raise ValueError
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            payload, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(
                signature, hmac.digest(self.cursor_secret, payload, "sha256")
            ):
                raise ValueError
            result = json.loads(payload)
            if any(
                result.get(k) != v
                for k, v in {"v": 1, "scope": _key(scope), "kind": kind, **expected}.items()
            ):
                raise ValueError
            return result
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise ViewCursorInvalid("cursor does not match this query") from error

    def _at(self, scope, boundary, generation):
        return self._encode(
            scope,
            "at",
            run=str(boundary.run_id),
            boundary=boundary.model_dump(mode="json"),
            generation=generation,
            algorithm=1,
            source=1,
        )

    def _decode_at(self, scope, run_id, at):
        data = self._decode(at, scope, "at", run=str(run_id), algorithm=1, source=1)
        try:
            return PlaybackBoundary.model_validate(data["boundary"]), data["generation"]
        except (KeyError, ValueError) as error:
            raise ViewCursorInvalid("invalid boundary") from error

    async def _view(self, session, scope, run_id, decoded=None, *, latest=None):
        if latest is None:
            latest = await self.port.capture_run(session, scope, run_id)
        generation = await self.port.active_generation(session, scope)
        if decoded and decoded[1] != generation:
            raise ViewRevisionExpired("view generation retired; reload current view")
        boundary = decoded[0] if decoded else latest
        snapshot = await self.port.prepare_read(session, scope, boundary, generation)
        page = assemble_view(
            scope=scope,
            boundary=boundary,
            state=snapshot.state,
            latest_available=latest.observed_at,
            missing_intervals=snapshot.missing_intervals,
        )
        page.at = self._at(scope, boundary, generation)
        first = await self.port.first_replayable(session, scope, boundary)
        page.run.first_replayable_cursor = self._at(scope, first, generation) if first else None
        return page, boundary, snapshot

    async def _step_page(self, session, scope, page, boundary, storage, filters, after, limit):
        result = await self.port.page_steps(
            session, scope, boundary, storage, filters, tuple(after) if after else None, limit + 1
        )
        selected = [
            assemble_step(boundary, row.step_id, row.payload) for row in result.rows[:limit]
        ]
        next_cursor = None
        if len(result.rows) > limit:
            last = result.rows[limit - 1]
            next_cursor = self._encode(
                scope,
                "steps",
                run=str(boundary.run_id),
                filters=filters,
                at=page.at,
                after=[last.observed_order, last.step_id],
            )
        return StepViewPage(
            items=selected,
            next_cursor=next_cursor,
            revision=boundary.projection_revision,
            completeness=page.run.completeness,
            hidden_count=result.hidden_count,
            at=page.at,
        )

    async def get_view(self, scope: OwnerScope, run_id: UUID, at: str | None = None) -> ViewPage:
        decoded = self._decode_at(scope, run_id, at) if at else None
        async with self.port.transaction(writable=True) as session:
            page, boundary, snapshot = await self._view(session, scope, run_id, decoded)
            steps = await self._step_page(
                session, scope, page, boundary, snapshot.steps, {}, None, 200
            )
            page.steps = steps.items
            page.next_cursor = steps.next_cursor
            return page

    async def get_production_cut(self, scope, run_id, at, rows):
        """Reuse exact reader authority; expose only run-local public observation ordering."""
        decoded = self._decode_at(scope, run_id, at)
        async with self.port.transaction(writable=True) as session:
            _page, boundary, _snapshot = await self._view(session, scope, run_id, decoded)
            observations = {}
            for row in rows:
                if (
                    row.binding_status != "bound"
                    or row.producer_run_id != run_id
                    or row.boundary is None
                    or row.boundary > boundary.formal_position
                ):
                    continue
                value = await self.port.production_observation(
                    session, scope, boundary, row.produced_event_id, row.boundary
                )
                if value is not None:
                    observations[str(row.produced_event_id)] = value
            return boundary, observations

    async def list_steps(
        self,
        scope: OwnerScope,
        run_id: UUID,
        revision: int | None = None,
        at: str | None = None,
        filters: dict | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> StepViewPage:
        filters = _filters(filters, "steps")
        _limit(limit, 500)
        after = None
        if cursor:
            data = self._decode(cursor, scope, "steps", run=str(run_id), filters=filters)
            if at and at != data["at"]:
                raise ViewCursorInvalid("page and at boundaries differ")
            at = data["at"]
            after = data["after"]
        decoded = self._decode_at(scope, run_id, at) if at else None
        if revision is not None and decoded and revision != decoded[0].projection_revision:
            raise ViewRevisionExpired("revision and at boundary differ")
        async with self.port.transaction(writable=True) as session:
            page, boundary, snapshot = await self._view(session, scope, run_id, decoded)
            if revision is not None and revision != boundary.projection_revision:
                raise ViewRevisionExpired("revision requires its exact at cursor")
            return await self._step_page(
                session, scope, page, boundary, snapshot.steps, filters, after, limit
            )

    async def get_step(
        self, scope: OwnerScope, run_id: UUID, step_id: str, at: str | None = None
    ) -> StepView:
        return (await self.get_step_cut(scope, run_id, step_id, at)).step

    async def get_step_cut(
        self, scope: OwnerScope, run_id: UUID, step_id: str, at: str | None = None
    ) -> StepCut:
        async with self.port.transaction(writable=True) as session:
            # Hide foreign Run identities before inspecting their historical token.
            # Reuse this authorized head in the same transaction for the fixed cut.
            latest = await self.port.capture_run(session, scope, run_id)
            decoded = self._decode_at(scope, run_id, at) if at else None
            _page, boundary, snapshot = await self._view(
                session, scope, run_id, decoded, latest=latest
            )
            effective = snapshot.state.get("replacement_step_ids", {}).get(step_id, step_id)
            row = await self.port.point_step(session, scope, boundary, snapshot.steps, effective)
            if row:
                return StepCut(
                    assemble_step(boundary, row.step_id, row.payload), boundary, _page.at
                )
            raise ViewNotFound("step does not exist at this boundary")

    async def list_runs(
        self,
        scope: OwnerScope,
        filters: dict | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> RunViewPage:
        filters = _filters(filters, "runs")
        _limit(limit, 200)
        data = self._decode(cursor, scope, "runs", filters=filters) if cursor else None
        async with self.port.transaction(writable=True) as session:
            generation = await self.port.active_generation(session, scope)
            cohort = (
                data["cohort"]
                if data
                else await self.port.capture_cohort(session, scope, filters, generation)
            )
            rows = await self.port.cohort_page(
                session, scope, cohort, data["after"] if data else None, limit + 1
            )
            items = []
            for row in rows[:limit]:
                boundary = PlaybackBoundary.model_validate(row.boundary)
                page, _, _ = await self._view(session, scope, row.run_id, (boundary, generation))
                items.append(page.run)
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = self._encode(
                    scope,
                    "runs",
                    filters=filters,
                    cohort=cohort,
                    after=[
                        last.admitted_at.isoformat() if last.admitted_at else None,
                        str(last.run_id),
                    ],
                )
            revision = self._encode(scope, "cohort", cohort=cohort, generation=generation)
            intervals = [i for item in items for i in item.completeness.missing_intervals]
            missing = sorted({f for item in items for f in item.completeness.missing_fields})
            return RunViewPage(
                items=items,
                next_cursor=next_cursor,
                revision=revision,
                completeness=Completeness(
                    state="partial" if intervals or missing else "complete",
                    missing_fields=missing,
                    missing_intervals=intervals,
                ),
            )

    async def get_timeline(
        self,
        scope: OwnerScope,
        run_id: UUID,
        start: datetime,
        end: datetime,
        target_time: datetime | None = None,
        direction: str = "before",
        bucket_count: int = 100,
        anchor_at: str | None = None,
    ) -> TimelineView:
        _limit(bucket_count, 200)
        if anchor_at is not None and target_time is not None:
            raise ViewCursorInvalid("anchor_at and target_time are mutually exclusive")
        anchor = self._decode_at(scope, run_id, anchor_at) if anchor_at else None
        if direction not in ("before", "after"):
            raise ViewCursorInvalid("direction must be before or after")
        times = _filters({"start": start, "end": end}, "runs")
        start = datetime.fromisoformat(times["start"])
        end = datetime.fromisoformat(times["end"])
        if target_time is not None:
            target_time = datetime.fromisoformat(_filters({"start": target_time}, "runs")["start"])
            if not start <= target_time <= end:
                raise ViewCursorInvalid("target must be within timeline range")
        async with self.port.transaction(writable=True) as session:
            page, boundary, _ = await self._view(session, scope, run_id)
            generation = await self.port.active_generation(session, scope)
            if anchor:
                await self._view(session, scope, run_id, anchor)
            timeline = await self.port.timeline(
                session,
                scope,
                boundary,
                start,
                end,
                target_time,
                direction,
                bucket_count,
                anchor[0].observed_order if anchor else None,
            )
            rows, selected, key_events = timeline.buckets, timeline.selected, timeline.key_events
            buckets = [
                TimelineBucket(
                    start=row.start,
                    end=row.end,
                    count=row.count,
                    formal_count=row.formal_count,
                    first_at=self._at(
                        scope, PlaybackBoundary.model_validate(row.first), generation
                    ),
                    last_at=self._at(scope, PlaybackBoundary.model_validate(row.last), generation),
                )
                for row in rows
            ]
            return TimelineView(
                run_id=run_id,
                revision=boundary.projection_revision,
                buckets=buckets,
                key_events=[
                    TimelineKeyEvent(
                        at=self._at(
                            scope, PlaybackBoundary.model_validate(item.boundary), generation
                        ),
                        observed_at=PlaybackBoundary.model_validate(item.boundary).observed_at,
                        kinds=item.kinds,
                    )
                    for item in key_events
                ],
                at=self._at(scope, PlaybackBoundary.model_validate(selected), generation)
                if selected
                else None,
                completeness=page.run.completeness,
                latest_available=page.run.latest_available,
            )
