"""Atomic kernel-only execution pools; no deadline can release a prepared lease."""

from sqlalchemy import text

from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable, ExecutionSlotPolicy
from app.domain.models.scope import Principal
from app.infrastructure.repositories.db_evaluation_budget_control_repository import (
    DBEvaluationBudgetControlRepository,
)
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    DBEvaluationDatasetRepository,
)


class DBEvaluationExecutionRepository:
    def __init__(self, session):
        self.db = session
        self.controls = DBEvaluationBudgetControlRepository(session)

    async def active_policy(self):
        body = await self.db.scalar(
            text(
                "SELECT v.body FROM evaluation_execution_policy_head h JOIN evaluation_execution_policy_versions v ON v.revision=h.revision WHERE h.singleton FOR UPDATE OF h"
            )
        )
        return ExecutionSlotPolicy.model_validate(body) if body else None

    async def bootstrap(self, policy):
        current = await self.active_policy()
        if current is None:
            if policy.revision != 1:
                raise ValueError("execution_policy_changed")
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_execution_policy_versions(revision,body) VALUES(1,CAST(:body AS jsonb)) ON CONFLICT DO NOTHING"
                ),
                {"body": policy.model_dump_json()},
            )
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_execution_policy_head(singleton,revision) VALUES(true,1) ON CONFLICT DO NOTHING"
                )
            )
            current = await self.active_policy()
        if current != policy:
            raise ValueError("execution_policy_changed")
        return current

    async def activate(self, policy, *, expected_revision):
        current = await self.active_policy()
        if current == policy and expected_revision == policy.revision - 1:
            return current
        if (
            current is None
            or current.revision != expected_revision
            or policy.revision != expected_revision + 1
        ):
            raise ValueError("execution_policy_changed")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_execution_policy_versions(revision,body) VALUES(:revision,CAST(:body AS jsonb))"
            ),
            {"revision": policy.revision, "body": policy.model_dump_json()},
        )
        await self.db.execute(
            text("UPDATE evaluation_execution_policy_head SET revision=:revision WHERE singleton"),
            {"revision": policy.revision},
        )
        return policy

    async def lock(self, scope, run_id, policy, *, require_policy=True):
        binding = await self.controls.binding(scope, run_id)
        if binding is None:
            raise ValueError("execution_budget_binding_missing")
        namespace = await self.controls.namespace(scope, binding.namespace_id, lock=True)
        current = await self.bootstrap(policy) if require_policy else await self.active_policy()
        if current is None:
            raise ValueError("execution_policy_unavailable")
        keys = (
            "0:global",
            "1:user:" + binding.requester["user_id"],
            "2:"
            + ("team:" + scope.team_id if scope.team_id else "user:" + scope.user_id)
            + ":"
            + binding.purpose,
        )
        limits = (
            current.global_limit,
            current.user_limit,
            current.subject_limit
            if binding.purpose == "evaluation_subject"
            else current.judge_limit,
        )
        counters = []
        for key, limit in zip(keys, limits, strict=True):
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_execution_pools(key,occupied) VALUES(:key,0) ON CONFLICT DO NOTHING"
                ),
                {"key": key},
            )
            occupied = await self.db.scalar(
                text("SELECT occupied FROM evaluation_execution_pools WHERE key=:key FOR UPDATE"),
                {"key": key},
            )
            counters.append((key, occupied, limit))
        lease = (
            (
                await self.db.execute(
                    text("SELECT * FROM evaluation_execution_leases WHERE run_id=:run FOR UPDATE"),
                    {"run": run_id},
                )
            )
            .mappings()
            .first()
        )
        return binding, namespace, current, counters, dict(lease) if lease else None

    async def authorize(self, scope, binding, namespace):
        if namespace.state != "open":
            raise ValueError("budget_namespace_closed")
        if binding.requester != namespace.requester:
            raise ValueError("budget_original_requester_required")
        await DBEvaluationDatasetRepository(self.db).authorize(
            scope, Principal.model_validate(binding.requester), write=True
        )

    async def acquire(self, counters):
        if any(limit is not None and occupied >= limit for _, occupied, limit in counters):
            raise ExecutionCapacityUnavailable("execution_capacity_unavailable")
        for key, _, _ in counters:
            await self.db.execute(
                text("UPDATE evaluation_execution_pools SET occupied=occupied+1 WHERE key=:key"),
                {"key": key},
            )

    async def prepare(self, scope, run_id, policy):
        from app.infrastructure.repositories.db_evaluation_lineage_repository import (
            DBEvaluationLineageRepository,
        )

        await DBEvaluationLineageRepository(self.db).ensure_initial(scope, run_id)
        binding, namespace, current, counters, lease = await self.lock(scope, run_id, policy)
        await self.authorize(scope, binding, namespace)
        if lease is not None:
            if lease["phase"] not in {"prepared", "held"}:
                raise ValueError("execution_lease_already_released")
            return lease
        await self.acquire(counters)
        await self.db.execute(
            text(
                "INSERT INTO evaluation_execution_leases(run_id,namespace_id,scope_key,phase,generation,accepted_version,run_generation,policy_revision) VALUES(:run,:namespace,:scope,'prepared',1,0,0,:policy)"
            ),
            {
                "run": run_id,
                "namespace": binding.namespace_id,
                "scope": "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id,
                "policy": current.revision,
            },
        )
        return dict(
            (
                await self.db.execute(
                    text("SELECT * FROM evaluation_execution_leases WHERE run_id=:run"),
                    {"run": run_id},
                )
            )
            .mappings()
            .one()
        )

    async def withdraw_unaccepted(self, scope, run_id, command_id, policy):
        """Kernel caller holds the durable batch cancellation fence before this call.

        Lock order matches acceptance. Only the exact original never-accepted
        Create may be withdrawn; missing projection or an elapsed lease is no proof.
        Physical/environment leases are deliberately independent of this operation.
        """
        from app.infrastructure.execution.postgres_event_store import PostgresEventStore

        trusted = await self.db.scalar(
            text(
                "SELECT opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
            )
        )
        if not trusted:
            raise PermissionError("kernel_withdrawal_required")
        binding, _, _, counters, lease = await self.lock(
            scope, run_id, policy, require_policy=False
        )
        inbox = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM execution_command_inbox WHERE command_id=:command FOR UPDATE"
                    ),
                    {"command": command_id},
                )
            )
            .mappings()
            .first()
        )
        if (
            inbox is None
            or inbox["command_type"] != "CreateRun"
            or inbox["stream_id"] != str(run_id)
            or inbox["owner_user_id"] != (None if scope.team_id else scope.user_id)
            or inbox["team_id"] != scope.team_id
        ):
            raise ValueError("admission_receipt_mismatch")
        if inbox["payload_digest"] is None:
            payload = inbox["payload"]
            if (
                payload.get("source_entity_id") != binding.source_entity_id
                or payload.get("source_entity_type") != binding.source_entity_type
                or payload.get("policy_snapshot", {}).get("snapshot_digest")
                != binding.policy_digest
            ):
                raise ValueError("admission_binding_mismatch")
        if inbox["status"] == "accepted":
            return False
        key = PostgresEventStore._scope_advisory_lock_key(
            None if scope.team_id else scope.user_id, scope.team_id
        )
        await self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        if await self.db.scalar(
            text(
                "SELECT 1 FROM execution_events WHERE stream_type='run' AND stream_id=:run LIMIT 1"
            ),
            {"run": str(run_id)},
        ):
            raise ValueError("admission_already_started")
        if (
            lease is None
            or lease["accepted_version"] != 0
            or lease["phase"] not in {"prepared", "released"}
        ):
            raise ValueError("admission_lease_not_prepared")
        if inbox["status"] not in {"rejected", "dead_lettered"}:
            await self.db.execute(
                text(
                    "UPDATE execution_command_inbox SET status='rejected',rejection_code='EVALUATION_WITHDRAWN',processed_at=clock_timestamp(),claim_deadline=NULL WHERE command_id=:command"
                ),
                {"command": command_id},
            )
        if lease["phase"] == "prepared":
            for key, _, _ in counters:
                await self.db.execute(
                    text(
                        "UPDATE evaluation_execution_pools SET occupied=occupied-1 WHERE key=:key"
                    ),
                    {"key": key},
                )
            await self.db.execute(
                text(
                    "UPDATE evaluation_execution_leases SET phase='released',generation=generation+1 WHERE run_id=:run"
                ),
                {"run": run_id},
            )
        return True
