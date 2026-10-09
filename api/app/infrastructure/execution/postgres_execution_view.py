"""Scoped execution reads and independently versioned, atomically activated shadows."""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.application.ports.execution_view import (
    CohortRun,
    ReadCut,
    RestoredState,
    ScopeViewHead,
    ShadowResult,
    StepStorage,
    StoredKeyEvent,
    StoredStep,
    StoredStepPage,
    StoredTimeline,
    StoredTimelineBucket,
    ViewNotFound,
    ViewRebuilding,
    ViewRevisionExpired,
)
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.postgres_playback import (
    PlaybackUnavailable,
    _merge_intervals,
    _target_relevant,
    load_playback,
    validate_playback_boundary,
)
from app.infrastructure.models.execution_view import ExecutionRunViewORM
from app.infrastructure.security.db_authorization import configure_session_authorization

SOURCE_VERSION = 1
ALGORITHM_VERSION = 1
COHORT_TTL = timedelta(minutes=15)
MAX_COHORT_RUNS = 100_000


def scope_key(scope):
    return f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"


def owner_params(scope):
    return {
        "scope": scope_key(scope),
        "owner": scope.user_id if scope.team_id is None else None,
        "team": scope.team_id,
    }


def boundary_of(run):
    return PlaybackBoundary(
        run_id=run.run_id,
        formal_position=run.formal_position,
        progress_position=run.progress_position,
        observed_order=run.observed_order,
        projection_revision=run.projection_revision,
        observed_at=run.as_of,
        projector_version=run.projector_version,
    )


async def writer_barrier(session, key):
    # MUST precede canonical Run lock. Shared holders do not serialize normal writers.
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 8404))"), {"key": key}
    )


async def _coverage(session, scope, boundary, *, evidence=None):
    """Metadata-only fingerprint of precisely the F03 retained prefix/coverage."""
    params = {
        "scope": scope_key(scope),
        "run": boundary.run_id,
        "cut": boundary.observed_order,
        "formal": boundary.formal_position,
        "progress": boundary.progress_position,
        "version": boundary.projector_version,
    }
    row = (
        await session.execute(
            text("""SELECT count(*) AS count,
        md5(coalesce(string_agg(observed_order::text,',' ORDER BY observed_order),'')) AS digest
        FROM execution_view_observations WHERE scope_key=:scope AND run_id=:run AND projector_version=:version
        AND observed_order<=:cut AND formal_position<=:formal AND progress_position<=:progress"""),
            params,
        )
    ).one()
    current = await session.scalar(
        text(
            "SELECT completeness->'missing_intervals' FROM execution_view_runs WHERE scope_key=:scope AND run_id=:run"
        ),
        params,
    )
    checkpoints = (
        (
            await session.execute(
                text("""SELECT state_ref->'missing_intervals' FROM execution_view_checkpoints
        WHERE scope_key=:scope AND run_id=:run AND projector_version=:version AND observed_order<=:cut
        AND formal_position<=:formal AND progress_position<=:progress"""),
                params,
            )
        )
        .scalars()
        .all()
    )
    if evidence is not None:
        evidence.retain(
            "view-coverage",
            {
                "boundary": boundary,
                "aggregate": row,
                "current": current,
                "checkpoints": checkpoints,
            },
        )
        evidence.reserve_state(len(current or []) + sum(len(items or []) for items in checkpoints))
    return coverage_from_originals(boundary, row, current, checkpoints)


def coverage_from_originals(boundary, row, current, checkpoints):
    """Same retained-prefix fingerprint for acquisition and private replay."""
    intervals = _merge_intervals(
        _target_relevant(current or [], boundary),
        *[_target_relevant(items or [], boundary) for items in checkpoints],
    )
    fingerprint = hashlib.sha256(
        json.dumps([row.digest, intervals], sort_keys=True, default=str).encode()
    ).hexdigest()
    return fingerprint, row.count, intervals


async def _save_shadow(session, scope, generation, boundary, snapshot, orders):
    params = {
        **owner_params(scope),
        "generation": generation,
        "run": boundary.run_id,
        "order": boundary.observed_order,
        "boundary": boundary.model_dump_json(),
        "state": json.dumps(snapshot.state),
        "missing": json.dumps(snapshot.missing_intervals, default=str),
    }
    params["coverage"] = (await _coverage(session, scope, boundary))[0]
    await session.execute(
        text("""INSERT INTO execution_view_shadow_runs
        (generation,run_id,observed_order,boundary,state,missing_intervals,coverage_token,owner_user_id,team_id,created_by)
        VALUES(CAST(:generation AS uuid),:run,:order,CAST(:boundary AS jsonb),CAST(:state AS jsonb),CAST(:missing AS jsonb),:coverage,:owner,:team,'view-projector')
        ON CONFLICT(generation,run_id) DO UPDATE SET observed_order=EXCLUDED.observed_order,boundary=EXCLUDED.boundary,
        state=EXCLUDED.state,missing_intervals=EXCLUDED.missing_intervals,coverage_token=EXCLUDED.coverage_token,updated_at=CURRENT_TIMESTAMP"""),
        params,
    )
    await session.execute(
        text(
            "DELETE FROM execution_view_shadow_steps WHERE scope_key=:scope AND generation=CAST(:generation AS uuid) AND run_id=:run"
        ),
        params,
    )
    steps = [
        {
            **params,
            "step": identity,
            "step_order": orders.get(identity, 0),
            "payload": json.dumps(payload),
        }
        for identity, payload in snapshot.state.get("step", {}).items()
    ]
    if steps:
        await session.execute(
            text("""INSERT INTO execution_view_shadow_steps(generation,run_id,step_id,observed_order,payload,owner_user_id,team_id,created_by)
            VALUES(CAST(:generation AS uuid),:run,:step,:step_order,CAST(:payload AS jsonb),:owner,:team,'view-projector')"""),
            steps,
        )


async def refresh_active_shadow(session, run, observation):
    from app.application.execution.playback import reduce_facts

    scope = (
        OwnerScope.team("execution-kernel", run.team_id)
        if run.team_id
        else OwnerScope.personal(run.owner_user_id)
    )
    params = {**owner_params(scope), "run": run.run_id}
    generation = await session.scalar(
        text("SELECT active_generation FROM execution_view_controls WHERE scope_key=:scope"), params
    )
    if generation is None:
        return
    params["generation"] = str(generation)
    boundary = boundary_of(run)
    previous = (
        await session.execute(
            text(
                "SELECT state,observed_order,boundary,coverage_token,missing_intervals FROM execution_view_shadow_runs WHERE scope_key=:scope AND run_id=:run AND generation=CAST(:generation AS uuid)"
            ),
            params,
        )
    ).first()
    valid_previous = (
        previous is not None and previous.observed_order == observation.observed_order - 1
    )
    if valid_previous:
        old_boundary = PlaybackBoundary.model_validate(previous.boundary)
        valid_previous = (
            previous.coverage_token == (await _coverage(session, scope, old_boundary))[0]
        )
    if not valid_previous:
        snapshot = await load_playback(session, boundary, trusted_scope=scope)
        await _save_shadow(
            session,
            scope,
            str(generation),
            boundary,
            snapshot,
            await _step_orders(session, scope, boundary),
        )
        return
    facts = [
        {
            **fact,
            "position": (observation.formal_position, observation.progress_position),
            "observed_order": observation.observed_order,
        }
        for fact in observation.public_payload.get("facts", [])
    ]
    state = reduce_facts(facts, boundary, initial_state=previous.state)
    coverage_token, _, newly_relevant = await _coverage(session, scope, boundary)
    # The prefix is validated above, but time advancement can expose metadata
    # absent at the old cut. Retain computed gaps from that validated prefix.
    missing_intervals = _merge_intervals(
        _target_relevant(previous.missing_intervals, boundary), newly_relevant
    )
    await session.execute(
        text(
            "UPDATE execution_view_shadow_runs SET state=CAST(:state AS jsonb),boundary=CAST(:boundary AS jsonb),observed_order=:order,coverage_token=:coverage,missing_intervals=CAST(:missing AS jsonb),updated_at=CURRENT_TIMESTAMP WHERE scope_key=:scope AND run_id=:run AND generation=CAST(:generation AS uuid)"
        ),
        {
            **params,
            "state": json.dumps(state),
            "boundary": boundary.model_dump_json(),
            "order": boundary.observed_order,
            "coverage": coverage_token,
            "missing": json.dumps(missing_intervals, default=str),
        },
    )
    for fact in facts:
        if fact["kind"] != "step":
            continue
        detail = {**params, "step": fact["id"], "order": boundary.observed_order}
        if fact["patch"].get("removed"):
            await session.execute(
                text(
                    "DELETE FROM execution_view_shadow_steps WHERE scope_key=:scope AND run_id=:run AND generation=CAST(:generation AS uuid) AND step_id=:step"
                ),
                detail,
            )
        else:
            await session.execute(
                text("""INSERT INTO execution_view_shadow_steps(generation,run_id,step_id,observed_order,payload,owner_user_id,team_id,created_by)
                VALUES(CAST(:generation AS uuid),:run,:step,:order,CAST(:payload AS jsonb),:owner,:team,'view-projector')
                ON CONFLICT(generation,run_id,step_id) DO UPDATE SET observed_order=EXCLUDED.observed_order,payload=EXCLUDED.payload,updated_at=CURRENT_TIMESTAMP"""),
                {**detail, "payload": json.dumps(state["step"][fact["id"]])},
            )


async def _step_orders(session, scope, boundary, *, evidence=None):
    rows = await session.execute(
        text("""SELECT f->>'id' AS step_id,max(o.observed_order) AS last_order
        FROM execution_view_observations o CROSS JOIN LATERAL jsonb_array_elements(o.public_payload->'facts') f
        WHERE o.scope_key=:scope AND o.run_id=:run AND o.projector_version=:version
        AND o.observed_order<=:cut AND f->>'kind'='step' GROUP BY f->>'id' """),
        {
            "scope": scope_key(scope),
            "run": boundary.run_id,
            "version": boundary.projector_version,
            "cut": boundary.observed_order,
        },
    )
    values = rows.all()
    if evidence is not None:
        evidence.retain("view-step-orders", {"boundary": boundary, "rows": values})
        evidence.reserve_state(len(values))
    return dict(values)


class PostgresExecutionView:
    async def production_observation(self, session, scope, boundary, event_id, formal_position):
        row = (
            await session.execute(
                text("""
            SELECT observed_order, observed_at FROM execution_view_observations
            WHERE scope_key=:scope AND run_id=:run AND projector_version=:version
              AND source_kind='formal' AND event_id=:event AND formal_position=:formal
              AND observed_order<=:cut
            ORDER BY observed_order LIMIT 1
        """),
                {
                    "scope": scope_key(scope),
                    "run": boundary.run_id,
                    "version": boundary.projector_version,
                    "event": event_id,
                    "formal": formal_position,
                    "cut": boundary.observed_order,
                },
            )
        ).one_or_none()
        return (
            {"production_order": row.observed_order, "production_observed_at": row.observed_at}
            if row
            else None
        )

    def __init__(self, *, session_factory, authorization, evidence=None):
        self.evidence = evidence
        self.session_factory = session_factory
        self.authorization = authorization

    @asynccontextmanager
    async def transaction(self, *, writable=False):
        async with self.session_factory() as session:
            # This factory creates an owned session. Authentication hooks may have
            # begun a transaction; reset it before selecting driver isolation.
            await session.rollback()
            await session.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ",
                    "postgresql_readonly": not writable,
                }
            )
            await configure_session_authorization(session, self.authorization)
            try:
                yield session
                if writable:
                    await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def active_generation(self, session, scope):
        value = await session.scalar(
            text("SELECT active_generation FROM execution_view_controls WHERE scope_key=:scope"),
            {"scope": scope_key(scope)},
        )
        if self.evidence is not None:
            self.evidence.retain("view-generation", value)
        return str(value) if value else "live"

    async def capture_run(self, session, scope, run_id):
        row = await session.scalar(
            select(ExecutionRunViewORM).where(
                ExecutionRunViewORM.scope_key == scope_key(scope),
                ExecutionRunViewORM.run_id == run_id,
            )
        )
        if self.evidence is not None:
            self.evidence.retain("view-capture", row)
        if row is None:
            raise ViewNotFound("run does not exist")
        if not row.observed_order or row.as_of is None:
            raise ViewRebuilding("run has no committed view boundary")
        return boundary_of(row)

    async def restore(self, session, scope, boundary, generation):
        try:
            await validate_playback_boundary(
                session, boundary, trusted_scope=scope, evidence=self.evidence
            )
            token, _, _ = await _coverage(session, scope, boundary, evidence=self.evidence)
            if generation != "live":
                shadow = (
                    await session.execute(
                        text("""SELECT state,missing_intervals FROM execution_view_shadow_runs
                    WHERE scope_key=:scope AND generation=CAST(:generation AS uuid) AND run_id=:run
                    AND observed_order=:cut AND coverage_token=:token"""),
                        {
                            "scope": scope_key(scope),
                            "generation": generation,
                            "run": boundary.run_id,
                            "cut": boundary.observed_order,
                            "token": token,
                        },
                    )
                ).first()
                if shadow:
                    return RestoredState(shadow.state, tuple(shadow.missing_intervals))
            snapshot = await load_playback(
                session, boundary, trusted_scope=scope, evidence=self.evidence
            )
            return RestoredState(snapshot.state, snapshot.missing_intervals)
        except PlaybackUnavailable as error:
            raise ViewRevisionExpired(str(error)) from error

    async def prepare_read(self, session, scope, boundary, generation):
        try:
            await validate_playback_boundary(
                session, boundary, trusted_scope=scope, evidence=self.evidence
            )
        except PlaybackUnavailable as error:
            raise ViewRevisionExpired(str(error)) from error
        token, _, _ = await _coverage(session, scope, boundary)
        params = {
            **owner_params(scope),
            "run": boundary.run_id,
            "cut": boundary.observed_order,
            "cut_text": str(boundary.observed_order),
            "generation": generation,
            "token": token,
        }
        if generation != "live":
            shadow = (
                await session.execute(
                    text("""SELECT state-'step' AS state,missing_intervals FROM execution_view_shadow_runs
                WHERE scope_key=:scope AND generation=CAST(:generation AS uuid) AND run_id=:run
                AND observed_order=CAST(:cut AS bigint) AND coverage_token=:token"""),
                    params,
                )
            ).first()
            if shadow:
                return ReadCut(
                    shadow.state, tuple(shadow.missing_intervals), StepStorage("shadow", generation)
                )
        cached = (
            await session.execute(
                text("""SELECT cut_id,state,missing_intervals FROM execution_view_read_cuts
            WHERE scope_key=:scope AND run_id=:run AND generation=:generation AND boundary->>'observed_order'=:cut_text
            AND coverage_token=:token AND expires_at>CURRENT_TIMESTAMP ORDER BY created_at DESC LIMIT 1"""),
                params,
            )
        ).first()
        if cached:
            return ReadCut(
                cached.state,
                tuple(cached.missing_intervals),
                StepStorage("cache", str(cached.cut_id)),
            )
        snapshot = await self.restore(session, scope, boundary, generation)
        orders = await _step_orders(session, scope, boundary)
        cut_id = str(uuid4())
        now = datetime.now(UTC)
        summary = {k: v for k, v in snapshot.state.items() if k != "step"}
        params.update(
            id=cut_id,
            state=json.dumps(summary),
            missing=json.dumps(snapshot.missing_intervals, default=str),
            boundary=boundary.model_dump_json(),
            now=now,
            expires=now + COHORT_TTL,
        )
        await session.execute(
            text("""INSERT INTO execution_view_read_cuts(cut_id,run_id,generation,boundary,state,missing_intervals,coverage_token,expires_at,owner_user_id,team_id,created_by,created_at)
            VALUES(CAST(:id AS uuid),:run,:generation,CAST(:boundary AS jsonb),CAST(:state AS jsonb),CAST(:missing AS jsonb),:token,:expires,:owner,:team,'view-query',:now)"""),
            params,
        )
        rows = [
            {
                **params,
                "step": identity,
                "order": orders.get(identity, 0),
                "payload": json.dumps(payload),
            }
            for identity, payload in snapshot.state.get("step", {}).items()
        ]
        if rows:
            await session.execute(
                text("""INSERT INTO execution_view_read_steps(cut_id,step_id,observed_order,payload,owner_user_id,team_id,created_by)
                VALUES(CAST(:id AS uuid),:step,:order,CAST(:payload AS jsonb),:owner,:team,'view-query')"""),
                rows,
            )
        return ReadCut(summary, snapshot.missing_intervals, StepStorage("cache", cut_id))

    def _step_source(self, scope, boundary, storage):
        params = {"scope": scope_key(scope), "run": boundary.run_id, "identity": storage.identity}
        if storage.kind == "shadow":
            return (
                "execution_view_shadow_steps",
                "scope_key=:scope AND run_id=:run AND generation=CAST(:identity AS uuid)",
                params,
            )
        return (
            "execution_view_read_steps",
            "scope_key=:scope AND cut_id=CAST(:identity AS uuid)",
            params,
        )

    async def page_steps(self, session, scope, boundary, storage, filters, after, limit):
        from app.infrastructure.execution.query_observation import named_query

        table, where, params = self._step_source(scope, boundary, storage)
        conditions = []
        allowed = {"kind", "status", "parent_step_id", "tool_name", "activity_id", "attempt_id"}
        for i, (key, value) in enumerate(filters.items()):
            if key not in allowed:
                raise ValueError("unsupported step filter")
            expression = (
                f"coalesce(payload->>'{key}','unknown')"
                if key in ("kind", "status")
                else f"payload->>'{key}'"
            )
            conditions.append(f"{expression}=:filter_{i}")
            params[f"filter_{i}"] = value
        filter_sql = " AND " + " AND ".join(conditions) if conditions else ""
        hidden = 0
        if conditions:
            hidden = await session.scalar(
                named_query(
                    text(
                        f"SELECT count(*) FROM {table} WHERE {where} AND NOT coalesce(({' AND '.join(conditions)}),false)"
                    ),
                    "steps.count",
                ),
                params,
            )
        key_sql = ""
        if after:
            params.update(last_order=after[0], last_id=after[1])
            key_sql = " AND (observed_order,step_id)<(:last_order,:last_id)"
        params["limit"] = limit
        rows = (
            await session.execute(
                named_query(
                    text(
                        f"SELECT step_id,observed_order,payload FROM {table} WHERE {where}{filter_sql}{key_sql} ORDER BY observed_order DESC,step_id DESC LIMIT :limit"
                    ),
                    "steps.page",
                ),
                params,
            )
        ).all()
        return StoredStepPage(
            [StoredStep(r.step_id, r.observed_order, r.payload) for r in rows], hidden
        )

    async def point_step(self, session, scope, boundary, storage, step_id):
        table, where, params = self._step_source(scope, boundary, storage)
        row = (
            await session.execute(
                text(
                    f"SELECT step_id,observed_order,payload FROM {table} WHERE {where} AND step_id=:step LIMIT 1"
                ),
                {**params, "step": step_id},
            )
        ).first()
        return StoredStep(row.step_id, row.observed_order, row.payload) if row else None

    async def step_orders(self, session, scope, boundary):
        return await _step_orders(session, scope, boundary)

    async def capture_cohort(self, session, scope, filters, generation):
        cohort = str(uuid4())
        now = datetime.now(UTC)
        params = {
            **owner_params(scope),
            "cohort": cohort,
            "generation": generation,
            "expires": now + COHORT_TTL,
            "now": now,
        }
        await session.execute(
            text("""INSERT INTO execution_view_cohorts(cohort_id,generation,expires_at,owner_user_id,team_id,created_by,created_at)
            VALUES(CAST(:cohort AS uuid),:generation,:expires,:owner,:team,'view-query',:now)"""),
            params,
        )
        conditions = ["scope_key=:scope", "observed_order>0"]
        for key, value in filters.items():
            column = {
                "state": "status",
                "mode": "execution_mode",
                "source_entity_type": "source->>'entity_type'",
                "source_entity_id": "source->>'entity_id'",
                "configuration": "configuration_revision",
                "start": "admitted_at",
                "end": "admitted_at",
            }.get(key, key)
            op = ">=" if key == "start" else "<=" if key == "end" else "="
            conditions.append(f"{column} {op} :filter_{key}")
            params["filter_" + key] = (
                datetime.fromisoformat(value) if key in ("start", "end") else value
            )
        where = " AND ".join(conditions)
        result = await session.execute(
            text(f"""INSERT INTO execution_view_cohort_runs(cohort_id,run_id,admitted_at,boundary,owner_user_id,team_id,created_by)
            SELECT CAST(:cohort AS uuid),run_id,admitted_at,jsonb_build_object('run_id',run_id,'formal_position',formal_position,
                'progress_position',progress_position,'observed_order',observed_order,'projection_revision',projection_revision,
                'observed_at',as_of,'projector_version',projector_version),owner_user_id,team_id,'view-query'
            FROM execution_view_runs WHERE {where} LIMIT {MAX_COHORT_RUNS + 1}"""),
            params,
        )
        if result.rowcount > MAX_COHORT_RUNS:
            raise ViewRebuilding("cohort exceeds 100000 runs; narrow time range")
        return cohort

    async def cohort_page(self, session, scope, cohort_id, after, limit):
        params = {"scope": scope_key(scope), "cohort": cohort_id, "limit": limit}
        cohort = (
            await session.execute(
                text(
                    "SELECT generation,expires_at FROM execution_view_cohorts WHERE scope_key=:scope AND cohort_id=CAST(:cohort AS uuid)"
                ),
                params,
            )
        ).first()
        if cohort is None or cohort.expires_at <= datetime.now(UTC):
            raise ViewRevisionExpired("cohort expired; restart listing")
        if cohort.generation != await self.active_generation(session, scope):
            raise ViewRevisionExpired("read generation retired")
        key = ""
        if after:
            params["last_id"] = UUID(after[1])
            params["last_time"] = datetime.fromisoformat(after[0]) if after[0] else None
            key = (
                " AND admitted_at IS NULL AND run_id<:last_id"
                if after[0] is None
                else " AND (admitted_at<:last_time OR admitted_at IS NULL OR (admitted_at=:last_time AND run_id<:last_id))"
            )
        rows = (
            await session.execute(
                text(
                    """SELECT run_id,admitted_at,boundary FROM execution_view_cohort_runs
            WHERE scope_key=:scope AND cohort_id=CAST(:cohort AS uuid)"""
                    + key
                    + " ORDER BY admitted_at DESC NULLS LAST,run_id DESC LIMIT :limit"
                ),
                params,
            )
        ).all()

        return [CohortRun(r.run_id, r.admitted_at, r.boundary) for r in rows]

    async def scope_head(self, session, scope):
        row = (
            await session.execute(
                text(
                    "SELECT coalesce(max(formal_position),0) AS formal_position,coalesce(sum(observed_order),0) AS observed_count,count(*) AS run_count FROM execution_view_runs WHERE scope_key=:scope"
                ),
                {"scope": scope_key(scope)},
            )
        ).one()
        return ScopeViewHead(int(row.formal_position), int(row.observed_count), row.run_count)

    async def rebuild_scope_shadow(self, scope, target_algorithm_version=1):
        if target_algorithm_version != ALGORITHM_VERSION:
            raise ValueError("unsupported read algorithm version")
        generation = str(uuid4())
        params = {**owner_params(scope), "generation": generation}
        async with self.transaction(writable=True) as session:
            captured = await session.scalar(
                text("SELECT count(*) FROM execution_view_runs WHERE scope_key=:scope"), params
            )
            captured_head = await self.scope_head(session, scope)
            previous = await self.active_generation(session, scope)
            await session.execute(
                text("""INSERT INTO execution_view_generations(generation,algorithm_version,source_version,status,captured_runs,owner_user_id,team_id,created_by)
                VALUES(CAST(:generation AS uuid),1,1,'building',:captured,:owner,:team,'view-rebuild')"""),
                {**params, "captured": captured},
            )
        caught_up = 0
        try:
            for _ in range(100):
                async with self.transaction() as session:
                    dirty = (
                        (
                            await session.execute(
                                text("""SELECT r.run_id FROM execution_view_runs r LEFT JOIN execution_view_shadow_runs s
                        ON s.generation=CAST(:generation AS uuid) AND s.run_id=r.run_id AND s.scope_key=r.scope_key
                        WHERE r.scope_key=:scope AND r.observed_order>0 AND (s.observed_order IS NULL OR s.observed_order<>r.observed_order)
                        ORDER BY r.run_id LIMIT 1000"""),
                                params,
                            )
                        )
                        .scalars()
                        .all()
                    )
                for run_id in dirty:
                    # One run at a time; no scope locks and no full-scope writer pause.
                    async with self.session_factory() as session:
                        await configure_session_authorization(session, self.authorization)
                        run = await session.scalar(
                            select(ExecutionRunViewORM)
                            .where(
                                ExecutionRunViewORM.scope_key == scope_key(scope),
                                ExecutionRunViewORM.run_id == run_id,
                            )
                            .with_for_update(key_share=True)
                        )
                        boundary = boundary_of(run)
                        snapshot = await load_playback(session, boundary, trusted_scope=scope)
                        if any(
                            i.get("reason") == "journal_observation_gap"
                            for i in snapshot.missing_intervals
                        ):
                            raise ViewRevisionExpired(
                                "cannot activate an incomplete journal rebuild"
                            )
                        await _save_shadow(
                            session,
                            scope,
                            generation,
                            boundary,
                            snapshot,
                            await _step_orders(session, scope, boundary),
                        )
                        await session.commit()
                        caught_up += 1
                activated_head = await self.activate_shadow(scope, generation, previous)
                if activated_head is not None:
                    return ShadowResult(
                        generation, 1, 1, captured, caught_up, True, captured_head, activated_head
                    )
            raise ViewRebuilding("writers outpaced bounded catch-up; active view preserved")
        except BaseException:
            async with self.transaction(writable=True) as session:
                await session.execute(
                    text(
                        "UPDATE execution_view_generations SET status='failed',updated_at=CURRENT_TIMESTAMP WHERE scope_key=:scope AND generation=CAST(:generation AS uuid) AND status='building'"
                    ),
                    params,
                )
            raise

    async def activate_shadow(self, scope, generation, expected_generation):
        # READ COMMITTED: acquire barrier before reading fresh committed heads.
        async with self.session_factory() as session:
            await configure_session_authorization(session, self.authorization)
            params = {**owner_params(scope), "generation": generation}
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,8404))"), params
            )
            if await self.active_generation(session, scope) != expected_generation:
                raise ViewRevisionExpired("another rebuild activated first")
            status = await session.scalar(
                text(
                    "SELECT status FROM execution_view_generations WHERE scope_key=:scope AND generation=CAST(:generation AS uuid)"
                ),
                params,
            )
            if status != "building":
                raise ViewRevisionExpired("shadow is not activatable")
            dirty = await session.scalar(
                text("""SELECT EXISTS(SELECT 1 FROM execution_view_runs r LEFT JOIN execution_view_shadow_runs s
                ON s.generation=CAST(:generation AS uuid) AND s.run_id=r.run_id AND s.scope_key=r.scope_key
                WHERE r.scope_key=:scope AND r.observed_order>0 AND (s.observed_order IS NULL OR s.observed_order<>r.observed_order))"""),
                params,
            )
            if dirty:
                return None
            await session.execute(
                text(
                    "UPDATE execution_view_generations SET status='retired',updated_at=CURRENT_TIMESTAMP WHERE scope_key=:scope AND status='active'"
                ),
                params,
            )
            await session.execute(
                text(
                    "UPDATE execution_view_generations SET status='active',updated_at=CURRENT_TIMESTAMP WHERE scope_key=:scope AND generation=CAST(:generation AS uuid)"
                ),
                params,
            )
            await session.execute(
                text("""INSERT INTO execution_view_controls(active_generation,owner_user_id,team_id,created_by)
                VALUES(CAST(:generation AS uuid),:owner,:team,'view-rebuild') ON CONFLICT(scope_key)
                DO UPDATE SET active_generation=EXCLUDED.active_generation,updated_at=CURRENT_TIMESTAMP"""),
                params,
            )
            head = await self.scope_head(session, scope)
            await session.commit()
            return head

    async def cleanup_expired(self, limit=1000):
        """Kernel-only bounded maintenance; never deletes source journal rows."""
        async with self.transaction(writable=True) as session:
            result = await session.execute(
                text("""DELETE FROM execution_view_cohorts WHERE cohort_id IN
                (SELECT cohort_id FROM execution_view_cohorts WHERE expires_at<CURRENT_TIMESTAMP ORDER BY expires_at LIMIT :limit)"""),
                {"limit": min(max(limit, 1), 1000)},
            )
            expired_cuts = await session.execute(
                text("""DELETE FROM execution_view_read_cuts WHERE cut_id IN
                (SELECT cut_id FROM execution_view_read_cuts WHERE expires_at<CURRENT_TIMESTAMP ORDER BY expires_at LIMIT :limit)"""),
                {"limit": min(max(limit, 1), 1000)},
            )
            return result.rowcount + expired_cuts.rowcount

    async def timeline(
        self, session, scope, boundary, start, end, target, direction, count, anchor_order=None
    ):
        params = {
            "scope": scope_key(scope),
            "run": boundary.run_id,
            "cut": boundary.observed_order,
            "version": boundary.projector_version,
            "start": start,
            "end": end,
            "target": target,
            "count": count,
            # asyncpg maps datetime.min/max to PostgreSQL infinities. Keep the
            # bounds for filtering, but bucket with a finite numeric offset.
            "start_epoch": (start - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds(),
            "width": max((end - start).total_seconds(), 0.000001),
        }
        where = "scope_key=:scope AND run_id=:run AND projector_version=:version AND observed_order<=:cut"
        boundary_json = """jsonb_build_object('run_id',run_id,'formal_position',formal_position,'progress_position',progress_position,
            'observed_order',observed_order,'projection_revision',projection_revision,'observed_at',observed_at,'projector_version',projector_version)"""
        rows = (
            await session.execute(
                text(f"""WITH bucketed AS (SELECT observed_at,observed_order,source_kind,{boundary_json} AS boundary,
            least(:count-1,floor((extract(epoch FROM observed_at)-:start_epoch)/:width*:count))::integer AS bucket
            FROM execution_view_observations WHERE {where} AND observed_at>=:start AND observed_at<=:end)
            SELECT bucket,min(observed_at) AS start,max(observed_at) AS end,count(*) AS count,
            count(*) FILTER(WHERE source_kind='formal') AS formal_count,
            (array_agg(boundary ORDER BY observed_at,observed_order))[1] AS first,
            (array_agg(boundary ORDER BY observed_at DESC,observed_order DESC))[1] AS last
            FROM bucketed GROUP BY bucket ORDER BY bucket"""),
                params,
            )
        ).all()
        selected = None
        if anchor_order is not None:
            params["anchor"] = anchor_order
            comparator, order = ("<", "DESC") if direction == "before" else (">", "ASC")
            selected = await session.scalar(
                text(f"""SELECT {boundary_json} FROM execution_view_observations
                WHERE {where} AND source_kind='formal' AND observed_order {comparator} :anchor
                ORDER BY observed_order {order} LIMIT 1"""),
                params,
            )
        if target is not None:
            comparator, order = ("<=", "DESC") if direction == "before" else (">=", "ASC")
            selected = await session.scalar(
                text(f"""SELECT {boundary_json} FROM execution_view_observations
                WHERE {where} AND observed_at {comparator} :target AND observed_at>=:start AND observed_at<=:end
                ORDER BY observed_at {order},observed_order {order} LIMIT 1"""),
                params,
            )
        key_events = (
            await session.execute(
                text(f"""SELECT {boundary_json} AS boundary,
            ARRAY(SELECT DISTINCT fact->>'kind' FROM jsonb_array_elements(public_payload->'facts') fact ORDER BY 1) AS kinds
            FROM execution_view_observations WHERE {where} AND observed_at>=:start AND observed_at<=:end AND source_kind='formal'
            ORDER BY observed_at DESC,observed_order DESC LIMIT 200"""),
                params,
            )
        ).all()
        return StoredTimeline(
            [
                StoredTimelineBucket(r.start, r.end, r.count, r.formal_count, r.first, r.last)
                for r in rows
            ],
            selected,
            [StoredKeyEvent(r.boundary, r.kinds) for r in reversed(key_events)],
        )

    async def first_replayable(self, session, scope, boundary):
        row = (
            (
                await session.execute(
                    text("""SELECT run_id,formal_position,progress_position,observed_order,projection_revision,observed_at,projector_version
            FROM execution_view_observations WHERE scope_key=:scope AND run_id=:run AND projector_version=:version AND observed_order=1"""),
                    {
                        "scope": scope_key(scope),
                        "run": boundary.run_id,
                        "version": boundary.projector_version,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        first = PlaybackBoundary.model_validate(dict(row))
        try:
            snapshot = await load_playback(session, first, trusted_scope=scope)
        except PlaybackUnavailable:
            return None
        run = snapshot.state.get("run", {}).get(str(boundary.run_id), {})
        return first if run.get("family") and run.get("status") else None
