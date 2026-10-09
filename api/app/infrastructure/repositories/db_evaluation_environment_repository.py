"""Caller-transaction environment metadata, CAS leases, and durable operation outbox."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from app.domain.evaluation.configuration import digest
from app.domain.evaluation.environment import (
    EnvironmentLease,
    EnvironmentOperation,
    EnvironmentVersion,
    TestCredentialRef,
    TestTarget,
    transition,
)
from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
from app.infrastructure.execution.original_evidence import retain_read
from app.infrastructure.repositories.db_evaluation_dataset_repository import params

MODELS = {"environment": EnvironmentVersion, "target": TestTarget, "credential": TestCredentialRef}


def registered_original(row, kind, revision=None, *, current=True):
    if (
        row is None
        or (revision is not None and row["revision"] != revision)
        or digest(row["body"]) != row["digest"]
    ):
        raise DatasetNotFound("environment_registry_unavailable")
    value = MODELS[kind].model_validate(row["body"])
    if current and not getattr(value, "enabled", True):
        raise DatasetNotFound("environment_registry_revoked")
    return value


class DBEvaluationEnvironmentRepository:
    def __init__(self, db_session):
        self.db = db_session

    async def list_versions(self, scope, *, after=None, limit=51):
        result = await self.db.execute(
            text(
                "SELECT body FROM evaluation_environment_registry WHERE scope_key=:scope AND kind='environment' AND NOT EXISTS(SELECT 1 FROM evaluation_resource_archives a WHERE a.scope_key=evaluation_environment_registry.scope_key AND a.kind='environment' AND a.resource_id=evaluation_environment_registry.id) AND (CAST(:after AS uuid) IS NULL OR id > :after) ORDER BY id LIMIT :limit"
            ),
            params(scope, after=after, limit=limit),
        )
        return [EnvironmentVersion.model_validate(body) for body in result.scalars().all()]

    async def lock(self, key):
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "environment:" + key},
        )

    async def register(self, scope, kind, value):
        model = MODELS[kind].model_validate(value)
        await self.lock(f"registry:{params(scope)['scope']}:{kind}:{model.id}")
        if kind == "environment" and model.revision != 1:
            raise ValueError("environment_version_id_immutable")
        previous = await self.db.scalar(
            text(
                "SELECT max(revision) FROM evaluation_environment_registry WHERE scope_key=:scope AND kind=:kind AND id=:id"
            ),
            params(scope, kind=kind, id=model.id),
        )
        if model.revision != (previous or 0) + 1:
            raise DatasetConflict("environment_revision_conflict")
        if kind == "target":
            endpoint = urlsplit(model.endpoint)
            origin = f"{endpoint.scheme}://{endpoint.hostname}:{endpoint.port or (443 if endpoint.scheme == 'https' else 80)}"
            await self.lock("physical:" + model.physical_resource)
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_environment_resources(physical_resource,origin,owner_user_id,team_id,created_by) VALUES(:physical,:origin,:owner,:team,:actor) ON CONFLICT DO NOTHING"
                ),
                params(scope, physical=model.physical_resource, origin=origin),
            )
            match = await self.db.scalar(
                text(
                    "SELECT 1 FROM evaluation_environment_resources WHERE physical_resource=:physical AND origin=:origin AND scope_key=:scope"
                ),
                params(scope, physical=model.physical_resource, origin=origin),
            )
            if not match:
                raise DatasetConflict("physical_target_scope_or_alias_conflict")
        body = model.model_dump(mode="json")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_environment_registry(id,kind,revision,body,digest,owner_user_id,team_id,created_by) VALUES(:id,:kind,:revision,CAST(:body AS jsonb),:digest,:owner,:team,:actor)"
            ),
            params(
                scope,
                id=model.id,
                kind=kind,
                revision=model.revision,
                body=json.dumps(body),
                digest=digest(body),
            ),
        )
        return model

    async def registered(self, scope, kind, identity, revision=None, *, current=True):
        result = await self.db.execute(
            text(
                "SELECT revision,body,digest FROM evaluation_environment_registry WHERE scope_key=:scope AND kind=:kind AND id=:id"
                + (" AND revision=:revision" if not current and revision is not None else "")
                + " ORDER BY revision DESC LIMIT 1"
            ),
            params(scope, kind=kind, id=identity, revision=revision),
        )
        try:
            row = result.mappings().first()
            retain_read(
                self.db,
                "version-source",
                "environment.registered",
                {
                    "scope": scope,
                    "kind": kind,
                    "id": identity,
                    "revision": revision,
                    "current": current,
                },
                row,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)
        return registered_original(row, kind, revision, current=current)

    async def lease(self, scope, identity, *, lock=False):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_environment_leases WHERE scope_key=:scope AND id=:id"
                        + (" FOR UPDATE" if lock else "")
                    ),
                    params(scope, id=identity),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DatasetNotFound("environment_lease_unavailable")
        return EnvironmentLease.model_validate(
            {key: row[key] for key in EnvironmentLease.model_fields}
        )

    async def allocate(self, scope, lease, targets, *, concurrency, capacity_policy=None):
        from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
        from app.infrastructure.repositories.db_environment_capacity_repository import (
            DBEnvironmentCapacityRepository,
        )

        # Same first barrier as environment archive. Hold it through allocation commit;
        # capacity/physical locks always follow it, never the reverse.
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "e12:" + params(scope)["scope"]},
        )
        policy = capacity_policy or EnvironmentCapacityPolicy()
        capacity = DBEnvironmentCapacityRepository(self.db)
        await capacity.lock_allocation(policy)
        await self.lock("capacity:" + params(scope)["scope"])
        previous = await self.db.scalar(
            text("SELECT id FROM evaluation_environment_leases WHERE scope_key=:scope AND id=:id"),
            params(scope, id=lease.id),
        )
        if previous:
            old = await self.lease(scope, lease.id)
            if (
                old.case_slot != lease.case_slot
                or old.environment_version != lease.environment_version
                or old.generation != lease.generation
            ):
                raise DatasetConflict("environment_lease_identity_conflict")
            return old
        await capacity.check(scope, lease, policy, requested_limit=concurrency)
        await self.db.execute(
            text(
                "INSERT INTO evaluation_environment_leases(id,environment_version,generation,revision,state,namespace,case_slot,requester,expires_at,owner_user_id,team_id,created_by) VALUES(:id,:version,:generation,:revision,'allocated',:namespace,CAST(:slot AS jsonb),CAST(:requester AS jsonb),:expires,:owner,:team,:actor)"
            ),
            params(
                scope,
                id=lease.id,
                version=lease.environment_version,
                generation=lease.generation,
                revision=lease.revision,
                namespace=lease.namespace,
                slot=lease.case_slot.model_dump_json(),
                requester=json.dumps(lease.requester),
                expires=lease.expires_at,
            ),
        )
        for physical in sorted({target.physical_resource for target in targets if target.shared}):
            await self.lock("physical:" + physical)
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_environment_fences(physical_resource,lease_id,generation,owner_user_id,team_id,created_by) VALUES(:physical,:id,:generation,:owner,:team,:actor) ON CONFLICT DO NOTHING"
                ),
                params(scope, physical=physical, id=lease.id, generation=lease.generation),
            )
            row = (
                (
                    await self.db.execute(
                        text(
                            "SELECT lease_id,generation FROM evaluation_environment_fences WHERE physical_resource=:physical AND scope_key=:scope FOR UPDATE"
                        ),
                        params(scope, physical=physical),
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["lease_id"] not in (None, lease.id):
                raise DatasetConflict("environment_physical_resource_busy")
            await self.db.execute(
                text(
                    "UPDATE evaluation_environment_fences SET lease_id=:id,generation=:generation WHERE physical_resource=:physical AND scope_key=:scope"
                ),
                params(scope, physical=physical, id=lease.id, generation=lease.generation),
            )
        return lease

    async def save(self, scope, old, new, *, error=None):
        result = await self.db.execute(
            text(
                "UPDATE evaluation_environment_leases SET state=:state,revision=:revision,repair_authorized=:repair,resources=CAST(:resources AS jsonb),actual_versions=CAST(:actual AS jsonb),error=:error WHERE scope_key=:scope AND id=:id AND generation=:generation AND revision=:expected"
            ),
            params(
                scope,
                id=old.id,
                generation=old.generation,
                expected=old.revision,
                state=new.state,
                revision=new.revision,
                repair=new.repair_authorized,
                resources=json.dumps(new.resources),
                actual=json.dumps(new.actual_versions),
                error=error,
            ),
        )
        if result.rowcount != 1:
            raise DatasetConflict("environment_lease_fence_lost")

    async def enqueue(self, scope, lease, phase):
        identity = uuid5(
            NAMESPACE_URL, f"environment:{lease.id}:{lease.generation}:{lease.revision}:{phase}"
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_environment_operations(id,lease_id,generation,lease_revision,phase,owner_user_id,team_id,created_by) VALUES(:id,:lease,:generation,:revision,:phase,:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            params(
                scope,
                id=identity,
                lease=lease.id,
                generation=lease.generation,
                revision=lease.revision,
                phase=phase,
            ),
        )
        return identity

    async def begin(self, scope, lease, state, phase, *, repair=False, administrator=False):
        new = transition(lease, state, repair=repair, administrator=administrator)
        await self.save(scope, lease, new)
        await self.enqueue(scope, new, phase)
        return new

    async def claim(self, scope, identity, *, now=None):
        now = now or datetime.now(UTC)
        # Read the immutable parent identity without an operation lock, then use
        # the same lease -> operation order as completion and cancellation.
        lease_id = await self.db.scalar(
            text(
                "SELECT lease_id FROM evaluation_environment_operations WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=identity),
        )
        if lease_id is None:
            return None
        lease = await self.lease(scope, lease_id, lock=True)
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_environment_operations WHERE scope_key=:scope AND id=:id FOR UPDATE"
                    ),
                    params(scope, id=identity),
                )
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["status"] not in {"queued", "running"}
            or (row["claim_until"] and row["claim_until"] > now)
        ):
            return None
        if row["status"] == "running":
            # A deadline cannot prove that a daemon-side mutation stopped.
            # Never overwrite the only identity capable of resolving this attempt.
            await self.db.execute(
                text(
                    "UPDATE evaluation_environment_operations SET status='superseded',error='environment_unknown_operation',claim_until=NULL WHERE scope_key=:scope AND id=:id"
                ),
                params(scope, id=identity),
            )
            if lease.state != "quarantine":
                await self.save(
                    scope,
                    lease,
                    transition(lease, "quarantine"),
                    error="environment_unknown_operation",
                )
            return None
        if (lease.generation, lease.revision) != (row["generation"], row["lease_revision"]):
            await self.db.execute(
                text(
                    "UPDATE evaluation_environment_operations SET status='superseded',error=CASE WHEN status='running' THEN 'environment_unknown_operation' ELSE error END WHERE scope_key=:scope AND id=:id"
                ),
                params(scope, id=identity),
            )
            return None
        generation = row["claim_generation"] + 1
        await self.db.execute(
            text(
                "UPDATE evaluation_environment_operations SET status='running',claim_generation=:claim,claim_until=:until WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=identity, claim=generation, until=now + timedelta(minutes=5)),
        )
        return EnvironmentOperation(
            id=row["id"],
            lease_id=lease.id,
            generation=lease.generation,
            lease_revision=lease.revision,
            phase=row["phase"],
            claim_generation=generation,
        ), lease

    async def complete(self, scope, operation, receipt, *, error=None):
        lease = await self.lease(scope, operation.lease_id, lock=True)
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_environment_operations WHERE scope_key=:scope AND id=:id FOR UPDATE"
                    ),
                    params(scope, id=operation.id),
                )
            )
            .mappings()
            .one()
        )
        if row["claim_generation"] == operation.claim_generation and (
            lease.generation,
            lease.revision,
        ) != (operation.generation, operation.lease_revision):
            # Only an unresolved exact attempt may acknowledge physical completion.
            # Duplicate terminal callbacks cannot reopen or rewrite settled outcomes.
            if row["status"] != "running" and row["error"] != "environment_unknown_operation":
                return False
            # Exact former worker acknowledgement never updates the newer lease/fence.
            await self.db.execute(
                text(
                    "UPDATE evaluation_environment_operations SET status='superseded',receipt=CAST(:receipt AS jsonb),error=:unknown,claim_until=NULL WHERE scope_key=:scope AND id=:id"
                ),
                params(
                    scope,
                    id=operation.id,
                    receipt=json.dumps(receipt),
                    unknown="environment_unknown_operation" if error else None,
                ),
            )
            return False
        if row["status"] != "running" or row["claim_generation"] != operation.claim_generation:
            return False
        if row["claim_until"] <= datetime.now(UTC):
            if lease.state != "quarantine":
                await self.save(
                    scope, lease, transition(lease, "quarantine"), error="environment_claim_expired"
                )
            await self.db.execute(
                text(
                    "UPDATE evaluation_environment_operations SET status='superseded',receipt=CAST(:receipt AS jsonb),error=:error,claim_until=NULL WHERE scope_key=:scope AND id=:id"
                ),
                params(
                    scope,
                    id=operation.id,
                    receipt=json.dumps(receipt),
                    error="environment_unknown_operation" if error else None,
                ),
            )
            return False
        await self.db.execute(
            text(
                "UPDATE evaluation_environment_operations SET status=:status,receipt=CAST(:receipt AS jsonb),error=:error,claim_until=NULL WHERE scope_key=:scope AND id=:id"
            ),
            params(
                scope,
                id=operation.id,
                status="failed" if error else "done",
                receipt=json.dumps(receipt),
                error=error,
            ),
        )
        if error:
            if lease.state != "quarantine":
                await self.save(scope, lease, transition(lease, "quarantine"), error=error)
            return True
        enriched = lease.model_copy(
            update={
                "resources": tuple(receipt.get("resources", lease.resources)),
                "actual_versions": receipt.get("actual_versions", lease.actual_versions),
            }
        )
        next_phase = {"prepare": "reset", "reset": "verify_ready", "cleanup": "verify_clean"}.get(
            operation.phase
        )
        if next_phase:
            await self.save(scope, lease, enriched)
            await self.enqueue(scope, enriched, next_phase)
        else:
            if lease.state == "quarantine":
                return True
            target = "ready" if operation.phase == "verify_ready" else "verified_clean"
            new = transition(enriched, target)
            await self.save(scope, lease, new)
            if target == "verified_clean":
                await self.db.execute(
                    text(
                        "UPDATE evaluation_environment_fences SET lease_id=NULL,generation=NULL WHERE scope_key=:scope AND lease_id=:id AND generation=:generation"
                    ),
                    params(scope, id=lease.id, generation=lease.generation),
                )
        return True

    async def pending(self, *, limit=20):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT id,owner_user_id,team_id,created_by FROM evaluation_environment_operations WHERE status IN ('queued','running') AND (claim_until IS NULL OR claim_until<CURRENT_TIMESTAMP) ORDER BY created_at LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )

    async def expired(self, *, limit=20):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT id,owner_user_id,team_id,created_by FROM evaluation_environment_leases WHERE state IN ('allocated','preparing','ready','leased') AND expires_at<CURRENT_TIMESTAMP ORDER BY expires_at LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )

    async def pending_count(self, scope, batch_id):
        return await self.db.scalar(
            text(
                "SELECT count(*) FROM evaluation_environment_leases WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch AND state IN ('cleaning','quarantine')"
            ),
            params(scope, batch=str(batch_id)),
        )

    async def bind(self, scope, run_id, lease, principal, admission):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_environment_bindings(run_id,lease_id,generation,principal,admission,actual_versions,owner_user_id,team_id,created_by) VALUES(:run,:lease,:generation,CAST(:principal AS jsonb),CAST(:admission AS jsonb),CAST(:actual AS jsonb),:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            params(
                scope,
                run=run_id,
                lease=lease.id,
                generation=lease.generation,
                principal=principal.model_dump_json(),
                admission=json.dumps(admission),
                actual=json.dumps(lease.actual_versions),
            ),
        )
        row = await self.binding(scope, run_id)
        if row != {
            "lease_id": lease.id,
            "generation": lease.generation,
            "principal": principal.model_dump(mode="json"),
            "admission": admission,
            "actual_versions": lease.actual_versions,
        }:
            raise DatasetConflict("environment_binding_changed")

    async def binding(self, scope, run_id):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT lease_id,generation,principal,admission,actual_versions FROM evaluation_environment_bindings WHERE scope_key=:scope AND run_id=:run"
                    ),
                    params(scope, run=run_id),
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def request_repair(self, scope, identity, lease, principal):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_environment_repairs(id,lease_id,generation,lease_revision,principal,owner_user_id,team_id,created_by) VALUES(:id,:lease,:generation,:revision,CAST(:principal AS jsonb),:owner,:team,:actor)"
            ),
            params(
                scope,
                id=identity,
                lease=lease.id,
                generation=lease.generation,
                revision=lease.revision,
                principal=principal.model_dump_json(),
            ),
        )

    async def repairs(self):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM evaluation_environment_repairs WHERE status='queued' ORDER BY created_at LIMIT 20"
                    )
                )
            )
            .mappings()
            .all()
        )

    async def settle_repair(self, scope, identity, *, accepted):
        await self.db.execute(
            text(
                "UPDATE evaluation_environment_repairs SET status=:status WHERE scope_key=:scope AND id=:id AND status='queued'"
            ),
            params(scope, id=identity, status="applied" if accepted else "rejected"),
        )

    async def cancel_operations(self, scope, lease):
        await self.lease(scope, lease.id, lock=True)
        unknown = await self.db.scalar(
            text(
                "SELECT count(*) FROM evaluation_environment_operations WHERE scope_key=:scope AND lease_id=:id AND generation=:generation AND status='running'"
            ),
            params(scope, id=lease.id, generation=lease.generation),
        )
        await self.db.execute(
            text(
                "UPDATE evaluation_environment_operations SET status='superseded' WHERE scope_key=:scope AND lease_id=:id AND generation=:generation AND status='queued'"
            ),
            params(scope, id=lease.id, generation=lease.generation),
        )
        return bool(unknown)

    async def unresolved_operations(self, scope, lease_id):
        return bool(
            await self.db.scalar(
                text(
                    "SELECT count(*) FROM evaluation_environment_operations WHERE scope_key=:scope AND lease_id=:id AND (status='running' OR error='environment_unknown_operation')"
                ),
                params(scope, id=lease_id),
            )
        )

    async def validate_connector(self, scope, target):
        from app.domain.models.integration_runtime import normalize_integration_tool_policies
        from app.domain.models.tool_policy import CONSERVATIVE_TOOL_POLICY, ToolExecutionPolicy

        pack = "mcp" if target.kind == "actuator" else target.kind
        if pack not in {"mcp", "a2a"}:
            raise ValueError("test_connector_kind_invalid")
        row = await self.db.scalar(
            text(
                f"SELECT to_jsonb(c) FROM {pack}_servers c WHERE id=:id AND enabled AND (visibility='global' OR (CAST(:team AS text) IS NOT NULL AND team_id=:team) OR (CAST(:team AS text) IS NULL AND owner_user_id=:owner AND team_id IS NULL))"
            ),
            params(scope, id=target.connector_id),
        )
        if row is None or digest(row) != target.connector_revision:
            raise ValueError("test_connector_changed")
        if pack == "mcp":
            if (
                row.get("url_encryption") != "plaintext"
                or row.get("url") != target.endpoint
                or row.get("transport") != "streamable_http"
                or row.get("headers")
                or row.get("env")
            ):
                raise ValueError("test_connector_endpoint_or_credentials_unavailable")
        elif row.get("base_url") != target.endpoint:
            raise ValueError("test_connector_endpoint_unavailable")
        policies = normalize_integration_tool_policies(
            {
                name: ToolExecutionPolicy.model_validate(value)
                for name, value in row.get("tool_policies", {}).items()
            }
        )
        for contract in target.contracts:
            key = contract.source_name if pack == "mcp" else contract.name
            if policies.get(key, CONSERVATIVE_TOOL_POLICY) != contract.policy:
                raise ValueError("test_connector_current_policy_mismatch")
