"""Real parity consumer with only persisted repositories/storage substituted."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.execution_capacity.inventory_readback import read_run
from scripts.test_benchmark_execution_visualization import _plan

from app.domain.execution.events import StoredEvent
from app.domain.execution.run import RunAggregate
from app.domain.execution.serialization import canonical_state_hash
from app.domain.execution.store import calculate_event_hash
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope


def completed_run(index=1000):
    plan = _plan(index)
    aggregate = RunAggregate()
    state = aggregate.initial_state(str(plan.run_id))
    events = []
    commands = list(plan.commands())
    for item in [commands[0], commands[1], commands[-1]]:
        command = item.bind().model_copy(update={"expected_stream_version": len(events)})
        decision = aggregate.decide(state, command)
        event = StoredEvent(
            **decision.events[0].model_dump(),
            position=len(events) + 1,
            event_id=UUID(int=len(events) + 1),
            stream_type="run",
            stream_id=str(plan.run_id),
            stream_version=len(events) + 1,
            owner_user_id=plan.owner_user_id,
            team_id=None,
            correlation_id=command.correlation_id,
            causation_id=command.command_id,
            occurred_at=command.issued_at,
            prev_hash=events[-1].event_hash if events else "0" * 64,
            event_hash="0" * 64,
        )
        event = event.model_copy(update={"event_hash": calculate_event_hash(event)})
        events.append(event)
        state = aggregate.evolve(state, event)
    return plan, state, events


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "watermark",
        "public_status",
        "public_purpose",
        "public_configuration",
        "extra_step",
        "missing_observations",
        "wrong_observation_identity",
        "duplicate_observation",
    ],
)
def test_complete_persisted_public_readback_cannot_fill_a_missing_or_wrong_projection(
    monkeypatch, mutation
):
    plan, state, events = completed_run()
    run = str(plan.run_id)
    projected = SimpleNamespace(
        stream_version=3,
        state_hash=canonical_state_hash(state),
        last_event_hash=events[-1].event_hash,
        terminal=True,
    )
    boundary = PlaybackBoundary(
        run_id=plan.run_id,
        formal_position=2 if mutation == "watermark" else 3,
        progress_position=0,
        observed_order=3,
        projection_revision=3,
        observed_at=events[-1].occurred_at,
        projector_version=1,
    )
    public = {
        "family": "ask",
        "status": "failed" if mutation == "public_status" else "completed",
        "purpose": "evaluation_subject" if mutation == "public_purpose" else "production",
        "configuration": {
            "configuration_revision": "wrong" if mutation == "public_configuration" else "config"
        },
        "source": None,
        "wait_reason": None,
        "capabilities": [],
        "completeness": {"state": "complete", "missing_fields": [], "missing_intervals": []},
    }
    restored = SimpleNamespace(state={"run": {run: public}, "step": {}}, missing_intervals=())

    class Port:
        def __init__(self, **kwargs):
            pass

        async def capture_run(self, *args):
            return boundary

        async def active_generation(self, *args):
            return "live"

        async def restore(self, *args):
            return restored

    monkeypatch.setattr("scripts.execution_capacity.inventory_readback.PostgresExecutionView", Port)

    class Facts:
        scope = OwnerScope.personal(plan.owner_user_id)
        scope_key = "user:" + plan.owner_user_id

        async def events(self, key):
            assert key == plan.run_id
            return events

        async def configuration(self, key, secret, **kwargs):
            return "config"

    class DB:
        async def scalar(self, *args):
            return projected

    class Query:
        db = DB()

        async def rows(self, name, *args):
            if name == "observations":
                return (
                    []
                    if mutation == "missing_observations"
                    else [
                        {
                            "observed_order": index + 1,
                            "projector_version": 1,
                            "source_kind": "formal",
                            "source_identity": "wrong"
                            if mutation == "wrong_observation_identity"
                            else str(events[0].event_id)
                            if mutation == "duplicate_observation"
                            else str(event.event_id),
                        }
                        for index, event in enumerate(events)
                    ]
                )
            if name == "steps" and mutation == "extra_step":
                return [{"step_id": "foreign", "status": "completed", "kind": "activity"}]
            return []

    async def collect():
        from scripts.execution_capacity import inventory_readback as readback

        inputs = readback.LiveRunInputs(Query(), Facts(), "private", None)
        return await readback.replay_run(inputs, run, "standard")

    if mutation is None:
        record, objects = asyncio.run(collect())
        assert record["formal_events"] == 3
        assert record["visible_steps"] == 0
        assert objects == []
        assert record["configuration_id"] == "config"
    else:
        with pytest.raises(
            ValueError, match=r"watermark|status|purpose|configuration|step|observation"
        ):
            asyncio.run(collect())


@pytest.mark.parametrize(
    "defect",
    [None, "request_ref", "request_digest", "request_generation", "result_ref", "claim_generation"],
)
def test_object_task_requires_actual_formal_request_and_settlement(defect):
    from scripts.execution_capacity.inventory_readback import validate_activity_parent

    requested = SimpleNamespace(
        event_type="ActivityRequested",
        public_payload={
            "activity_id": "activity",
            "generation": 2,
            "input_ref": "execution/input",
            "input_digest": "digest",
        },
    )
    completed = SimpleNamespace(
        event_type="ActivityCompleted",
        public_payload={
            "activity_id": "activity",
            "generation": 2,
            "result_ref": "execution/result",
            "claim_generation": 3,
        },
    )
    task = {
        "activity_id": "activity",
        "request_generation": 2,
        "request_ref": "execution/input",
        "request_digest": "digest",
        "status": "succeeded",
        "result_ref": "execution/result",
        "claim_generation": 3,
    }
    if defect:
        task[defect] = "wrong"
        with pytest.raises(ValueError, match="formal activity parent"):
            validate_activity_parent(task, [requested, completed])
    else:
        validate_activity_parent(task, [requested, completed])


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "extra_step",
        "extra_step_empty",
        "missing_step",
        "wrong_step",
        "stale_step",
        "wrong_step_generation",
        "missing_run",
        "stale_run",
        "wrong_run_generation",
        "wrong_run_state",
        "wrong_run_boundary",
        "wrong_coverage",
        "wrong_step_order",
        "legacy",
    ],
)
def test_actual_active_shadow_storage_matches_source_not_restore_fallback(monkeypatch, defect):
    from copy import deepcopy

    from scripts.execution_capacity.inventory_sql import InventoryQueries

    from app.infrastructure.execution import postgres_execution_view as view

    plan, formal, events = completed_run()
    run, generation = str(plan.run_id), str(UUID(int=55))
    boundary = PlaybackBoundary(
        run_id=plan.run_id,
        formal_position=3,
        progress_position=0,
        observed_order=3,
        projection_revision=3,
        observed_at=events[-1].occurred_at,
        projector_version=1,
    )
    payload = {"kind": "phase", "status": "completed"}
    source = {
        "run": {
            run: {
                "family": "ask",
                "status": "completed",
                "purpose": "production",
                "configuration": {"configuration_revision": "config"},
                "completeness": {
                    "state": "complete",
                    "missing_fields": [],
                    "missing_intervals": [],
                },
            }
        },
        "step": {"actual-step": payload},
    }
    projected = SimpleNamespace(
        stream_version=3,
        state_hash=canonical_state_hash(formal),
        last_event_hash=events[-1].event_hash,
        terminal=True,
    )
    live = SimpleNamespace(
        run_id=plan.run_id,
        formal_position=3,
        progress_position=0,
        observed_order=3,
        projection_revision=3,
        as_of=boundary.observed_at,
        projector_version=1,
    )
    shadow = {
        "generation": generation,
        "run_id": plan.run_id,
        "scope_key": "user:" + plan.owner_user_id,
        "observed_order": 3,
        "boundary": boundary.model_dump(mode="json"),
        "state": deepcopy(source),
        "missing_intervals": [],
        "coverage_token": "actual-coverage",
    }
    steps = [
        {
            "generation": generation,
            "run_id": plan.run_id,
            "scope_key": shadow["scope_key"],
            "step_id": "actual-step",
            "observed_order": 2,
            "payload": deepcopy(payload),
        }
    ]
    if defect == "extra_step_empty":
        source["step"] = {}
        shadow["state"]["step"] = {}
        steps[0]["step_id"] = "orphan-shadow-step"
    elif defect == "extra_step":
        steps.append({**steps[0], "step_id": "orphan-shadow-step"})
    elif defect == "missing_step":
        steps.clear()
    elif defect == "wrong_step":
        steps[0]["step_id"] = "wrong-step"
    elif defect == "stale_step":
        steps[0]["payload"]["status"] = "running"
    elif defect == "wrong_step_generation":
        steps[0]["generation"] = str(UUID(int=56))
    elif defect == "stale_run":
        shadow["observed_order"] = 2
    elif defect == "wrong_run_generation":
        shadow["generation"] = str(UUID(int=56))
    elif defect == "wrong_run_state":
        shadow["state"]["run"][run]["wait_reason"] = "foreign-state"
    elif defect == "wrong_run_boundary":
        shadow["boundary"]["formal_position"] = 2
    elif defect == "wrong_coverage":
        shadow["coverage_token"] = "old-coverage"
    elif defect == "wrong_step_order":
        steps[0]["observed_order"] = 1

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def one(self):
            assert len(self.rows) == 1
            return self.rows[0]

        def first(self):
            return SimpleNamespace(**self.rows[0]) if self.rows else None

    class DB:
        async def stream(self, sql, params=None):
            result = await self.execute(sql, params)

            class Stream:
                def mappings(self):
                    return self

                async def __aiter__(self):
                    for row in result.rows:
                        yield row

                async def close(self):
                    pass

            return Stream()

        async def scalar(self, sql, params=None):
            sql = str(sql)
            if "execution_run_projection" in sql:
                return projected
            if "execution_view_controls" in sql:
                return None if defect == "legacy" else generation
            if "execution_view_runs" in sql:
                return live
            raise AssertionError(sql)

        async def execute(self, sql, params=None):
            sql = str(sql)
            if "row_to_json(q)" in sql:
                original = sql.split("FROM (", 1)[1].rsplit(") AS q", 1)[0]
                rows = (await self.execute(original, params)).rows
                sizes = [len(str(r).encode()) for r in rows]
                return Result(
                    [
                        {
                            "row_count": len(rows),
                            "max_bytes": max(sizes, default=0),
                            "total_bytes": sum(sizes),
                            "read_only": "on",
                            "isolation": "repeatable read",
                            "snapshot": "fixture",
                        }
                    ]
                )
            assert sql.lstrip().startswith("SELECT"), "inventory attempted a public read-cut write"
            if "execution_view_shadow_runs" in sql:
                matching = defect != "missing_run" and shadow["generation"] == params["generation"]
                if "AND observed_order=" in sql:
                    matching &= (
                        shadow["observed_order"] == params["cut"]
                        and shadow["coverage_token"] == params["token"]
                    )
                return Result([shadow] if matching else [])
            if "execution_view_shadow_steps" in sql:
                return Result(
                    [
                        row
                        for row in steps
                        if row["generation"] == params.get("identity", params.get("generation"))
                    ]
                )
            if "execution_view_steps" in sql:
                return Result(
                    [] if defect == "extra_step_empty" else [{**payload, "step_id": "actual-step"}]
                )
            if "execution_view_observations" in sql:
                return Result(
                    [
                        {
                            "observed_order": i + 1,
                            "projector_version": 1,
                            "source_kind": "formal",
                            "source_identity": str(event.event_id),
                        }
                        for i, event in enumerate(events)
                    ]
                )
            if "execution_content_bindings" in sql or "execution_activity_tasks" in sql:
                return Result([])
            raise AssertionError(sql)

    async def coverage(*args, evidence=None):
        return "actual-coverage", 3, []

    async def validate(*args, **kwargs):
        pass

    async def playback(*args, **kwargs):
        return SimpleNamespace(state=deepcopy(source), missing_intervals=())

    async def orders(*args, evidence=None):
        return {"actual-step": 2}

    monkeypatch.setattr(view, "_coverage", coverage)
    monkeypatch.setattr(view, "validate_playback_boundary", validate)
    monkeypatch.setattr(view, "load_playback", playback)
    monkeypatch.setattr(view, "_step_orders", orders)

    class Facts:
        scope = OwnerScope.personal(plan.owner_user_id)
        scope_key = "user:" + plan.owner_user_id

        async def events(self, key):
            return events

        async def configuration(self, *args, **kwargs):
            return "config"

    async def collect():
        return await read_run(InventoryQueries(DB()), Facts(), run, "standard", "private", None)

    if defect in {None, "legacy"}:
        record, _ = asyncio.run(collect())
        assert record["visible_steps"] == 1
        assert record["generation"] == ("live" if defect == "legacy" else generation)
    else:
        with pytest.raises(ValueError, match=r"shadow|step|persisted"):
            asyncio.run(collect())
