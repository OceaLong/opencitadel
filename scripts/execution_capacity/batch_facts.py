"""Exact committed batch parents observed before physical object writes.

All SQL here is read-only. Durable service transactions, never this inventory,
create dispatch candidates, judge intents, leases and formal execution facts.
"""

import asyncio
import json
import re
import time
from uuid import UUID, uuid5

from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.inventory_sql import IDENTITY, plain, read_snapshot
from scripts.execution_capacity.observers import ObservedStorage
from scripts.execution_capacity.persistence import PersistedFacts
from sqlalchemy import text

LEASE_SQL = "SELECT * FROM evaluation_environment_leases WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch AND (:final OR id != ALL(CAST(:clean AS uuid[])))"
ATTEMPT_SQL = """SELECT a.run_id,a.attempt FROM evaluation_batch_attempts a
JOIN evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id
WHERE r.scope_key=:scope AND r.batch_id=:batch AND r.case_revision_id=:case
AND r.config_version_id=:config AND r.repetition=:repeat"""
OPERATION_SQL = "SELECT * FROM evaluation_environment_operations WHERE scope_key=:scope AND lease_id=:id ORDER BY created_at,id"


def lease_observation_clean(lease, attempts, operations, scope):
    """Shared historical attempt/lease/operation predicate, with no I/O."""
    if lease.case_slot.workspace != scope or not any(
        uuid5(UUID(str(a["run_id"])), "environment") == lease.id
        and a["attempt"] + 1 == lease.generation
        for a in attempts
    ):
        raise ValueError("lease does not match exact actual case attempt")
    return (
        lease.state == "verified_clean"
        and bool(operations)
        and all(
            operation["status"] == "done"
            and operation["error"] is None
            and bool(operation["receipt"])
            and operation["claim_until"] is None
            for operation in operations
        )
    )


def own_run_parent(subjects, judges, scope_key, batch_id):
    if len(subjects) + len(judges) != 1:
        raise ValueError("object run has no unique committed owned batch parent")
    row = dict((subjects or judges)[0])
    if subjects and row["intent"] is None:
        raise ValueError("subject admission intent not committed before put")
    parent = json.loads(json.dumps(row, default=str))
    return {
        "scope": scope_key,
        "batch_id": str(batch_id),
        "kind": "subject" if subjects else "judge",
        "parent": parent,
    }


class BatchFacts(PersistedFacts):
    def __init__(self, *args, batch_id, parent_kind="batch", **kwargs):
        super().__init__(*args, **kwargs)
        self.batch_id = UUID(str(batch_id))
        self.journal.parent(parent_kind, self.batch_id)
        self.baseline_verified = False
        self.clean_leases = {
            UUID(identity)
            for identity, row in self.journal.records("lease")
            if row["receipt"] is not None
        }
        self.lease_total = len(self.clean_leases)

    async def own_run(self, run_id, *, record=True):
        run_id = UUID(str(run_id))
        async with self.session() as session:
            subjects = (
                (
                    await session.execute(
                        text("""
                SELECT a.result_id,a.attempt,a.intent,r.case_revision_id,r.config_version_id,r.repetition
                FROM evaluation_batch_attempts a JOIN evaluation_batch_results r
                  ON r.scope_key=a.scope_key AND r.id=a.result_id
                WHERE a.scope_key=:scope AND a.run_id=:run AND r.batch_id=:batch
            """),
                        {"scope": self.scope_key, "run": run_id, "batch": self.batch_id},
                    )
                )
                .mappings()
                .all()
            )
            judges = (
                (
                    await session.execute(
                        text("""
                SELECT id,result_id,candidate FROM evaluation_judge_intents
                WHERE scope_key=:scope AND run_id=:run AND batch_id=:batch
            """),
                        {"scope": self.scope_key, "run": run_id, "batch": self.batch_id},
                    )
                )
                .mappings()
                .all()
            )
        if self.evidence is not None:
            self.evidence.retain(
                "batch-source",
                {
                    "operation": "batch.own_run",
                    "scope": self.scope_key,
                    "batch_id": self.batch_id,
                    "run_id": run_id,
                    "subjects": subjects,
                    "judges": judges,
                },
            )
        body = own_run_parent(subjects, judges, self.scope_key, self.batch_id)
        if record:
            self.journal.intent("run", run_id, body)
        return body

    async def own_object_parent(self, key):
        match = re.fullmatch(
            r"execution/(inputs|results)/([0-9a-f-]{36})/([0-9a-f]{64})\.json", key
        )
        if match is None:
            raise ValueError("unexpected batch object; retained without write")
        if match[1] == "inputs":
            await self.own_run(match[2])
            return
        async with self.session() as session:
            row = (
                (
                    await session.execute(
                        text("""
                SELECT aggregate_id,owner_user_id,team_id,request_generation,claim_generation,status
                FROM execution_activity_tasks WHERE activity_id=:id
            """),
                        {"id": UUID(match[2])},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if self.evidence is not None:
            self.evidence.retain(
                "batch-source",
                {
                    "operation": "batch.own_object_parent",
                    "key": key,
                    "scope": self.scope_key,
                    "claim": row,
                },
            )
        if (
            row is None
            or row["owner_user_id"] != self.scope.user_id
            or row["team_id"] is not None
            or row["status"] not in {"claimed", "call_started"}
        ):
            raise ValueError("object activity lacks actual owned active claim")
        await self.own_run(row["aggregate_id"])
        self.journal.intent(
            "activity",
            match[2],
            {
                "scope": self.scope_key,
                "run_id": row["aggregate_id"],
                "generation": row["request_generation"],
            },
        )
        self.journal.intent(
            "batch_claim",
            f"{match[2]}:{row['claim_generation']}",
            {
                "activity_id": match[2],
                "run_id": row["aggregate_id"],
                "generation": row["request_generation"],
                "claim_generation": row["claim_generation"],
            },
        )

    async def environments(self, *, final=False):
        """Reconcile all attempts including superseded operation receipts."""
        from app.domain.evaluation.environment import EnvironmentLease

        async with read_snapshot(
            self.sessions, self.authorization, budget=self.journal.budget
        ) as query:
            observation = {
                "batch_id": str(self.batch_id),
                "scope": self.scope_key,
                "final": final,
                "start_ns": time.monotonic_ns(),
                "reads": [],
                "query_observations": [],
                "leases": [],
                "attempts": {},
                "operations": {},
                "error": None,
            }
            # Kept on the journal immediately after each complete observation;
            # failures are retained by the enclosing final acquisition owner.
            try:
                observation["database"] = plain(await query.rows("database", IDENTITY))
                rows = await query.rows(
                    "leases",
                    LEASE_SQL,
                    {
                        "scope": self.scope_key,
                        "batch": str(self.batch_id),
                        "final": final,
                        "clean": list(self.clean_leases),
                    },
                )
                observation["leases"] = plain(rows)
                leases = []
                clean = True
                for row in rows:
                    lease = EnvironmentLease.model_validate(
                        {key: row[key] for key in EnvironmentLease.model_fields}
                    )
                    attempts = await query.rows(
                        "attempts:" + str(lease.id),
                        ATTEMPT_SQL,
                        {
                            "scope": self.scope_key,
                            "batch": self.batch_id,
                            "case": lease.case_slot.case_id,
                            "config": lease.case_slot.config_version,
                            "repeat": lease.case_slot.repeat - 1,
                        },
                    )
                    observation["attempts"][str(lease.id)] = plain(attempts)
                    body = lease.model_dump(mode="json")
                    self.journal.intent(
                        "lease_state",
                        f"{lease.id}:{lease.revision}",
                        {
                            "lease_id": str(lease.id),
                            "revision": lease.revision,
                            "state": lease.state,
                        },
                    )
                    self.journal.intent(
                        "lease",
                        lease.id,
                        {
                            "scope": self.scope_key,
                            "batch_id": str(self.batch_id),
                            "namespace": lease.namespace,
                            "environment_version": str(lease.environment_version),
                            "generation": lease.generation,
                            "case_slot": body["case_slot"],
                        },
                    )
                    operations = await query.rows(
                        "operations:" + str(lease.id),
                        OPERATION_SQL,
                        {"scope": self.scope_key, "id": lease.id},
                    )
                    observation["operations"][str(lease.id)] = plain(operations)
                    for operation in operations:
                        raw = json.loads(json.dumps(dict(operation), default=str))
                        # Revision/claim snapshots retain late/superseded receipts,
                        # never overwrite earlier unknown evidence.
                        from hashlib import sha256

                        self.journal.intent(
                            "environment_observation",
                            str(operation["id"])
                            + ":"
                            + sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                            raw,
                        )
                    lease_clean = lease_observation_clean(
                        lease, attempts, operations, self.scope_key
                    )
                    clean &= lease_clean
                    if lease_clean:
                        self.journal.acknowledge(
                            "lease",
                            lease.id,
                            {"state": "verified_clean", "namespace": lease.namespace},
                        )
                        self.clean_leases.add(lease.id)
                    leases.append(lease)
            except Exception as error:
                observation["error"] = type(error).__name__
                raise
            finally:
                observation["reads"] = query.finish_outputs()
                if query.original_owner is None:
                    before_bytes = query.budget.bytes
                    query.budget.charge(query.originals)
                    query.budget.reserve((query.budget.bytes - before_bytes) * 64, rows=0)
                    observation["query_observations"] = plain(query.originals)
                    observation["end_ns"] = time.monotonic_ns()
                    self.journal.intent(
                        "environment_read", canonical_digest(observation), observation
                    )
                else:
                    from scripts.execution_capacity.original_plain import (
                        canonical_original_digest,
                        plain_graph,
                    )

                    observation["query_observations"] = plain_graph(
                        query.originals, owner=query.original_owner, budget=query.budget
                    )
                    observation["end_ns"] = time.monotonic_ns()
                    identity = canonical_original_digest(
                        observation, owner=query.original_owner, budget=query.budget
                    )
                    self.journal.intent(
                        "environment_read", identity, observation, body_owner=query.original_owner
                    )
        return leases, bool(leases if final else leases or self.clean_leases) and clean

    async def assert_owned(self, *, final=False):
        await self.host_fence()
        async with self.session() as session:
            for sql in (
                "SELECT count(*) FROM execution_poisoned_runs",
                "SELECT count(*) FROM execution_poisoned_scopes",
                "SELECT count(*) FROM evaluation_recording_jobs",
                "SELECT count(*) FROM execution_exports",
                "SELECT count(*) FROM comparison_sets",
                "SELECT count(*) FROM knowledge_bases",
                "SELECT count(*) FROM files",
                "SELECT count(*) FROM artifact_production_receipts WHERE event_id IS NULL",
                "SELECT count(*) FROM artifact_upload_intents WHERE cleaned_at IS NULL",
                "SELECT count(*) FROM scheduled_jobs WHERE enabled",
                "SELECT count(*) FROM notification_deliveries",
                "SELECT count(*) FROM patrol_runs",
                "SELECT count(*) FROM patrol_remediations",
            ):
                if await session.scalar(text(sql)):
                    raise ValueError("unrelated operational work in batch deployment")
            if await session.scalar(
                text("SELECT count(*) FROM evaluation_batches WHERE id<>:batch"),
                {"batch": self.batch_id},
            ):
                raise ValueError("foreign batch in dedicated deployment")
            if await session.scalar(
                text(
                    "SELECT count(*) FROM evaluation_environment_leases WHERE scope_key<>:scope OR case_slot->>'batch_id'<>:batch"
                ),
                {"scope": self.scope_key, "batch": str(self.batch_id)},
            ):
                raise ValueError("foreign environment lease in dedicated deployment")
            runs = (
                (
                    await session.execute(
                        text(
                            "SELECT s.stream_id,p.stream_version,p.terminal,p.owner_user_id,p.team_id FROM execution_stream_owners s LEFT JOIN execution_run_projection p ON p.run_id::text=s.stream_id"
                            if final or not self.baseline_verified
                            else "SELECT run_id::text AS stream_id,stream_version,terminal,owner_user_id,team_id FROM execution_run_projection WHERE NOT terminal"
                        )
                    )
                )
                .mappings()
                .all()
            )
        for row in runs:
            run = row["stream_id"]
            prior = self.journal.get("run", run)
            if prior is None:
                await self.own_run(run)
            elif prior["body"].get("batch_id") != str(self.batch_id):
                sealed = prior["receipt"]
                if (
                    sealed is None
                    or not row["terminal"]
                    or row["stream_version"] != sealed["formal_events"]
                    or row["team_id"] is not None
                    or "user:" + row["owner_user_id"] != prior["body"]["scope"]
                ):
                    raise ValueError("unsealed or changed prior history in full kernel deployment")
        self.baseline_verified = True
        return await self.environments(final=final)


class BatchStorage(ObservedStorage):
    """Five started SDK awaiters; normal worker/admission task bounds remain."""

    def __init__(self, delegate, journal, facts, *, queue_limit):
        super().__init__(delegate, journal)
        self.facts = facts
        self.slots = asyncio.Semaphore(5)
        self.queued = 0
        self.queue_limit = queue_limit

    async def put_bytes(self, key, data):
        await self.facts.own_object_parent(key)
        if self.queued >= self.queue_limit:
            raise RuntimeError("bounded batch object queue exceeded")
        self.queued += 1
        acquired = False
        try:
            await self.slots.acquire()
            acquired = True
            await super().put_bytes(key, data)
        except BaseException:
            # Once acquired, a cancelled SDK may still be writing. Keep its
            # capacity slot quarantined until final drain; never replace it.
            if acquired:
                acquired = False
            raise
        finally:
            self.queued -= 1
            if acquired:
                self.slots.release()

    async def delete_bytes(self, key):
        # Preserve ordinary DatasetObjectLifecycle cleanup, whose UoW owns the
        # object lock and reference checks. Execution objects remain retained.
        async with self.facts.session() as session:
            dataset = await session.scalar(
                text(
                    "SELECT dataset_id FROM evaluation_object_intents WHERE scope_key=:scope AND storage_key=:key"
                ),
                {"scope": self.facts.scope_key, "key": key},
            )
        parent = self.journal.parent("batch", self.facts.batch_id)
        if dataset is None or str(dataset) != parent["dataset_entity_id"]:
            raise ValueError("object lacks exact dataset lifecycle parent")
        return await self.delegate.delete_bytes(key)
