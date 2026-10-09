"""Authoritative, read-only persisted recovery and source/view parity checks."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text

from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.execution.serialization import canonical_state_hash
from app.infrastructure.execution.models import ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.security.db_authorization import configure_session_authorization


def verify_admission_configuration(rows, *, scope, run_id, purpose, signing_secret):
    """Replay the original admission body and requester HMAC without a session."""
    if not isinstance(signing_secret, str) or not signing_secret:
        raise ValueError("original requester verification material required")
    if len(rows) != 1 or rows[0]["purpose"] != purpose:
        raise ValueError("actual admission configuration missing or ambiguous")
    row = rows[0]
    from app.domain.models.execution_usage import content_revision
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )

    if (
        content_revision({"run_id": str(run_id), "purpose": row["purpose"], "body": row["body"]})
        != row["id"]
    ):
        raise ValueError("configuration identity digest differs")
    proof = DBPhysicalRequesterRepository(None, signing_secret=signing_secret).verify(
        row["body"].get("physical_requester"), scope=scope, run_id=run_id
    )
    if proof.get("kind") != "user" or proof.get("principal", {}).get("user_id") != scope.user_id:
        raise ValueError("admitted requester differs")
    return str(row["id"])


class PersistedFacts:
    def __init__(self, sessions, authorization, journal, scope, host_fence, *, evidence=None):
        self.sessions, self.authorization, self.journal, self.scope = (
            sessions,
            authorization,
            journal,
            scope,
        )
        self.scope_key = "user:" + scope.user_id
        self.host_fence = host_fence
        self.evidence = evidence

    @asynccontextmanager
    async def session(self):
        async with self.sessions() as session:
            await configure_session_authorization(session, self.authorization)
            yield session

    async def events(self, run_id):
        self.journal.parent("run", run_id)
        return await self.retained_events(run_id)

    async def retained_events(self, run_id):
        """Read retained facts after the caller has checked an actual persisted parent."""
        async with self.session() as session:
            events = await PostgresEventStore(session, evidence=self.evidence).load_stream(
                "run", str(run_id)
            )
        if any(
            event.owner_user_id != self.scope.user_id or event.team_id is not None
            for event in events
        ):
            raise ValueError("actual source parent belongs to another scope")
        return events

    async def command(self, identity, run_id):
        async with self.session() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM execution_command_inbox WHERE command_id=:id"),
                        {"id": UUID(str(identity))},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        fields = {key: row[key] for key in CommandEnvelope.model_fields}
        envelope = CommandEnvelope.model_validate(fields)
        if (
            envelope.stream_id != str(run_id)
            or envelope.owner_user_id != self.scope.user_id
            or envelope.team_id is not None
        ):
            raise ValueError("recovered command ownership differs")
        captured = self.journal.get("command", identity)
        if captured and captured["body"] != envelope.model_dump(mode="json"):
            raise ValueError("persisted command differs from original envelope")
        self.journal.intent("command", identity, envelope.model_dump(mode="json"))
        return envelope, row["status"]

    async def task(self, activity_id):
        parent = self.journal.parent("activity", activity_id)
        async with self.session() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM execution_activity_tasks WHERE activity_id=:id"),
                        {"id": UUID(str(activity_id))},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is not None and (
            row["aggregate_id"] != parent["run_id"]
            or row["owner_user_id"] != self.scope.user_id
            or row["team_id"] is not None
            or row["request_generation"] != 0
        ):
            raise ValueError("recovered task authority differs")
        return row

    async def configuration(self, run_id, signing_secret, *, purpose="production", record=True):
        async with self.session() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id,body,purpose FROM execution_configurations WHERE scope_key=:scope AND run_id=:run AND body->>'stage'='admission'"
                        ),
                        {"scope": self.scope_key, "run": UUID(str(run_id))},
                    )
                )
                .mappings()
                .all()
            )
        if self.evidence is not None:
            self.evidence.retain("signed-configuration", rows)
        verified = verify_admission_configuration(
            rows, scope=self.scope, run_id=run_id, purpose=purpose, signing_secret=signing_secret
        )
        row = rows[0]
        if record:
            self.journal.intent(
                "configuration",
                row["id"],
                {
                    "scope": self.scope_key,
                    "run_id": str(run_id),
                    "body": row["body"],
                    "purpose": row["purpose"],
                },
            )
        return verified

    async def content(self, identity, expected):
        async with self.session() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT content_id,run_id,activity_id,generation,claim_generation,phase FROM execution_public_content WHERE scope_key=:scope AND command_id=:command AND phase=:phase"
                        ),
                        {
                            "scope": self.scope_key,
                            "command": UUID(expected["command_id"]),
                            "phase": expected["phase"],
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        for key in ("run_id", "activity_id", "generation", "claim_generation", "phase"):
            if str(row[key]) != str(expected[key]):
                raise ValueError("recovered content claim differs")
        self.journal.acknowledge("content", identity, {"content_id": str(row["content_id"])})
        return row

    async def reconcile_activity(self, activity_id, handler):
        """Recover stable settlement IDs before considering another real claim."""
        task = await self.task(activity_id)
        if task is None:
            raise ValueError("requested task missing")
        run_id = task["aggregate_id"]
        for kind in ("CompleteActivity", "FailActivity", "MarkActivityOutcomeUnknown"):
            identity = uuid5(NAMESPACE_URL, f"opencitadel:{activity_id}:{kind}")
            captured = self.journal.get("command", identity)
            persisted = await self.command(identity, run_id)
            if persisted or captured:
                envelope = (
                    persisted[0] if persisted else CommandEnvelope.model_validate(captured["body"])
                )
                result = await handler.handle(envelope)
                if result.status != "accepted" or kind != "CompleteActivity":
                    raise ValueError("historical settlement was not successful")
                task = await self.task(activity_id)
                break
        for identity, content in self.journal.activity_children("content", activity_id):
            await self.content(identity, content["body"])
        if task["status"] == "succeeded":
            events = await self.events(run_id)
            identity = uuid5(NAMESPACE_URL, f"opencitadel:{activity_id}:CompleteActivity")
            if (
                sum(
                    e.causation_id == identity and e.event_type == "ActivityCompleted"
                    for e in events
                )
                != 1
            ):
                raise ValueError("settlement lacks exact formal source")
            return True
        if task["claim_generation"] != 0 or task["status"] != "pending":
            # A result object or call-started row cannot reconstruct a missing
            # outcome. Reclaim would append an extra start and conflict with the
            # stable settlement identity; retain this interrupted fixture.
            raise ValueError("claimed historical activity requires unresolved recovery")
        return False

    async def baseline(self):
        async with self.session() as session:
            for stream in (
                await session.execute(text("SELECT stream_id FROM execution_stream_owners"))
            ).all():
                self.journal.parent("run", stream[0])

    async def assert_exclusive(self):
        await self.host_fence()
        async with self.session() as session:
            # No producer other than this dedicated one-off may hold a DB client.
            if await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid() AND (client_addr IS DISTINCT FROM inet_client_addr() OR usename<>current_user)"
                )
            ):
                raise ValueError("foreign database client")
            for sql in (
                "SELECT stream_id AS id FROM execution_command_inbox WHERE status IN ('received','processing','dead_lettered')",
                "SELECT aggregate_id AS id FROM execution_activity_tasks WHERE status IN ('pending','claimed','call_started','dead_lettered','unknown')",
                "SELECT command_envelope->>'stream_id' AS id FROM execution_scheduled_commands WHERE status IN ('pending','fired','dead_lettered')",
                "SELECT run_id::text AS id FROM execution_run_projection WHERE decision_due_at IS NOT NULL AND NOT terminal",
                "SELECT e.stream_id AS id FROM execution_outbox o JOIN execution_events e ON e.position=o.event_position WHERE o.delivered_at IS NULL",
            ):
                for row in (await session.execute(text(sql))).all():
                    self.journal.parent("run", row[0])
            # Fresh historical construction has no evaluation/physical work.
            for sql in (
                "SELECT count(*) FROM evaluation_batches",
                "SELECT count(*) FROM evaluation_environment_leases WHERE state!='verified_clean'",
                "SELECT count(*) FROM execution_model_dispatches",
                "SELECT count(*) FROM execution_poisoned_runs",
                "SELECT count(*) FROM execution_poisoned_scopes",
                "SELECT count(*) FROM evaluation_recording_jobs",
                "SELECT count(*) FROM evaluation_object_intents",
                "SELECT count(*) FROM execution_exports",
                "SELECT count(*) FROM comparison_sets",
                "SELECT count(*) FROM knowledge_bases",
                "SELECT count(*) FROM files",
                "SELECT count(*) FROM artifact_production_receipts WHERE event_id IS NULL",
                "SELECT count(*) FROM artifact_upload_intents WHERE cleaned_at IS NULL",
                "SELECT count(*) FROM artifact_retired_objects WHERE cleaned_at IS NULL",
                "SELECT count(*) FROM scheduled_jobs WHERE enabled",
                "SELECT count(*) FROM notification_deliveries",
                "SELECT count(*) FROM patrol_runs",
                "SELECT count(*) FROM patrol_remediations",
            ):
                if await session.scalar(text(sql)):
                    raise ValueError("unrelated or unresolved deployment work")

    async def parity(self, run_id, count, steps, views):
        events = await self.events(run_id)  # store verifies retained hash chain
        if len(events) != count:
            raise ValueError("standard formal event count differs")
        aggregate = RunAggregate()
        state = aggregate.initial_state(str(run_id))
        for event in events:
            if event.owner_user_id != self.scope.user_id or event.team_id is not None:
                raise ValueError("source scope differs")
            state = aggregate.evolve(state, event)
        if state.status.value != "completed" or state.active_activity_ids:
            raise ValueError("historical source not terminal")
        async with self.session() as session:
            projected = await session.scalar(
                select(ExecutionRunProjectionORM).where(ExecutionRunProjectionORM.run_id == run_id)
            )
            if (
                projected is None
                or projected.stream_version != count
                or projected.state_hash != canonical_state_hash(state)
                or projected.last_event_hash != events[-1].event_hash
            ):
                raise ValueError("canonical formal projection differs")
            if await session.scalar(
                text(
                    "SELECT count(*) FROM execution_activity_tasks WHERE aggregate_id=:run AND (status!='succeeded' OR claim_deadline IS NOT NULL OR claimed_by IS NOT NULL)"
                ),
                {"run": str(run_id)},
            ):
                raise ValueError("historical tasks did not converge")
            timers = [
                uuid5(NAMESPACE_URL, f"opencitadel:activity-timeout:{activity}:0")
                for activity, _kind, _generation in state.requested_activities
            ]
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT timer_id,status,command_envelope FROM execution_scheduled_commands WHERE timer_id=ANY(:ids)"
                        ),
                        {"ids": timers},
                    )
                )
                .mappings()
                .all()
            )
            if len(rows) != steps or any(
                row["status"] != "cancelled" or row["command_envelope"]["stream_id"] != str(run_id)
                for row in rows
            ):
                raise ValueError("historical timers did not converge")
            for row in rows:
                self.journal.acknowledge("timer", row["timer_id"], {"status": "cancelled"})
        page = await views.get_view(self.scope, run_id)
        if page.run.status.value != "completed":
            raise ValueError("public view not completed")
        visible = list(page.steps)
        cursor = page.next_cursor
        while cursor:
            step_page = await views.list_steps(
                self.scope, run_id, cursor=cursor, revision=page.revision
            )
            visible.extend(step_page.items)
            cursor = step_page.next_cursor
        if len(visible) != steps or len({s.activity_id for s in visible}) != steps:
            raise ValueError("source/public step count differs")
        for step in visible:
            if step.status != "completed" or step.input_ref is None or step.output_ref is None:
                raise ValueError("public step outcome differs")
        return {"formal_events": count, "visible_steps": steps, "terminal": True}

    async def drain_outbox(self, outbox):
        await self.assert_exclusive()
        for _ in range(100):
            stats = await outbox.dispatch_batch(limit=1000, now=datetime.now(UTC))
            if stats.failed:
                raise ValueError("owned outbox delivery failed")
            if not stats.claimed:
                break
        else:
            raise ValueError("outbox did not converge")

    async def converge(self, outbox):
        await self.drain_outbox(outbox)
        async with self.session() as session:
            for query in (
                "SELECT count(*) FROM execution_activity_tasks WHERE status IN ('pending','claimed','call_started','unknown','dead_lettered') OR claimed_by IS NOT NULL OR claim_deadline IS NOT NULL",
                "SELECT count(*) FROM execution_scheduled_commands WHERE status NOT IN ('cancelled')",
                "SELECT count(*) FROM execution_run_projection WHERE NOT terminal",
                "SELECT count(*) FROM execution_command_inbox WHERE status IN ('received','processing','dead_lettered')",
                "SELECT count(*) FROM execution_outbox WHERE delivered_at IS NULL",
            ):
                if await session.scalar(text(query)):
                    raise ValueError("owned operational work remains unconverged")

    async def standard_totals(self, fixture_id):
        async with self.session() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT count(*) AS runs, sum(stream_version) AS events, count(*) FILTER(WHERE stream_version=10000) AS hotspots, bool_and(terminal AND status='completed') AS completed FROM execution_run_projection WHERE source_entity_type='capacity_fixture' AND source_entity_id=:fixture"
                        ),
                        {"fixture": str(fixture_id)},
                    )
                )
                .mappings()
                .one()
            )
            formal = await session.scalar(
                text(
                    "SELECT count(*) FROM execution_events e JOIN execution_run_projection p ON p.run_id::text=e.stream_id AND e.stream_type='run' WHERE e.owner_scope_key=:scope AND p.source_entity_type='capacity_fixture' AND p.source_entity_id=:fixture"
                ),
                {"scope": self.scope_key, "fixture": str(fixture_id)},
            )
        if (row["runs"], row["events"], row["hotspots"], row["completed"], formal) != (
            100_000,
            10_000_000,
            10,
            True,
            10_000_000,
        ):
            raise ValueError("actual standard source/projection totals differ")
