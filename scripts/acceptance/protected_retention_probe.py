"""Mounted exact-batch, authorized, read-only complete retention snapshot.

Only already-owned evaluation runs are queried. Private scoring and formal audit
rows are retained in mode-0600 runner artifacts; credentials are never queried.
"""

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parent))


class ProbeFailure(Exception):
    """Static diagnostic context only: never SQL, parameters or source payloads."""

    def __init__(self, stage, table, error, diagnostic_source=None):
        self.diagnostic_source = diagnostic_source
        self.diagnostic = safe_error(error, stage=stage, table=table)
        super().__init__(stage)


def safe_error(error, *, stage="probe", table=None):
    diagnostic = {"stage": stage, "exception": type(error).__name__}
    if table is not None:
        diagnostic["table"] = table
    original = getattr(error, "orig", error)
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
        diagnostic["sqlstate"] = state
    if isinstance(error, ValueError):
        message = str(error)
        prefix = "protected retention: "
        # Our validator constructs these from fixed strings/table names only.
        from protected_retention import SAFE_REASONS

        if message.startswith(prefix) and message[len(prefix) :] in SAFE_REASONS:
            diagnostic["reason"] = message[len(prefix) :]
    return diagnostic


def source_digest():
    return hashlib.sha256(
        b"".join(
            (Path(__file__).parent / name).read_bytes()
            for name in ("protected_retention_probe.py", "protected_retention.py")
        )
    ).hexdigest()


async def snapshot(session, batch_id, owner_id):
    from protected_retention import validate_snapshot
    from sqlalchemy import text

    parameters = {"scope": "user:" + owner_id, "owner": owner_id, "batch": batch_id}
    tables = {}

    async def rows(name, query):
        # Full ordered typed rows, not an unverifiable audit/scoring digest.
        try:
            tables[name] = list(await session.scalars(text(query), parameters))
        except Exception as error:  # Preserve static context only.
            raise ProbeFailure("source-table", name, error) from error
        return tables[name]

    await rows(
        "batches",
        "SELECT to_jsonb(t) FROM evaluation_batches t WHERE scope_key=:scope AND id=CAST(:batch AS uuid) ORDER BY id",
    )
    await rows(
        "results",
        "SELECT to_jsonb(t) FROM evaluation_batch_results t WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid) ORDER BY id",
    )
    await rows(
        "attempts",
        "SELECT to_jsonb(t) FROM evaluation_batch_attempts t WHERE scope_key=:scope AND result_id IN (SELECT id FROM evaluation_batch_results WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid)) ORDER BY result_id,attempt",
    )
    await rows(
        "intents",
        "SELECT to_jsonb(t) FROM evaluation_judge_intents t WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid) ORDER BY id",
    )
    parameters["runs"] = sorted({row["run_id"] for row in tables["attempts"] + tables["intents"]})
    parameters["intents"] = [row["id"] for row in tables["intents"]]
    parameters["namespaces"] = sorted(
        {batch_id} | {row["namespace_id"] for row in tables["intents"]}
    )
    parameters["resources"] = sorted(
        {batch_id} | set(parameters["runs"]) | {row["id"] for row in tables["results"]}
    )
    await rows(
        "judge_work",
        "SELECT to_jsonb(t) FROM evaluation_judge_work t WHERE scope_key=:scope AND intent_id::text=ANY(:intents) ORDER BY intent_id",
    )
    await rows(
        "invalidations",
        "SELECT to_jsonb(t) FROM evaluation_judge_invalidations t WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid) ORDER BY intent_id,source_set_id",
    )
    await rows(
        "bindings",
        "SELECT to_jsonb(t) FROM evaluation_budget_bindings t WHERE scope_key=:scope AND run_id::text=ANY(:runs) ORDER BY run_id",
    )
    await rows(
        "namespaces",
        "SELECT to_jsonb(t) FROM evaluation_budget_namespaces t WHERE scope_key=:scope AND id::text=ANY(:namespaces) ORDER BY id",
    )
    # Request bodies can contain credentials; preserve their digest plus every
    # dispatch identity/ordinal/configuration instead of reading body values out.
    await rows(
        "dispatches",
        "SELECT (to_jsonb(t)-'request_snapshot') || jsonb_build_object('request_snapshot_sha256',encode(public.digest(convert_to(request_snapshot::text,'UTF8'),'sha256'),'hex')) FROM execution_model_dispatches t WHERE scope_key=:scope AND run_id::text=ANY(:runs) ORDER BY call_identity",
    )
    parameters["calls"] = [row["call_identity"] for row in tables["dispatches"]]
    await rows(
        "reservations",
        "SELECT to_jsonb(t) FROM evaluation_budget_reservations t WHERE scope_key=:scope AND (call_identity::text=ANY(:calls) OR demand->>'batch_id'=:batch) ORDER BY call_identity",
    )
    await rows(
        "settlements",
        "SELECT to_jsonb(t) FROM execution_model_settlements t WHERE scope_key=:scope AND call_identity=ANY(:calls) ORDER BY call_identity",
    )
    await rows(
        "execution_leases",
        "SELECT to_jsonb(t) FROM evaluation_execution_leases t WHERE scope_key=:scope AND run_id::text=ANY(:runs) ORDER BY run_id",
    )
    await rows(
        "environment_leases",
        "SELECT to_jsonb(t) FROM evaluation_environment_leases t WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch ORDER BY id",
    )
    await rows(
        "score_sets",
        "SELECT to_jsonb(t) FROM evaluation_score_sets t WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid) ORDER BY evaluation_revision,id",
    )
    parameters["sets"] = [row["id"] for row in tables["score_sets"]]
    await rows(
        "scores",
        "SELECT to_jsonb(t) FROM evaluation_scores t WHERE scope_key=:scope AND set_id::text=ANY(:sets) ORDER BY set_id,dimension,id",
    )
    await rows(
        "batch_events",
        "SELECT to_jsonb(t) FROM evaluation_batch_events t WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid) ORDER BY revision,id",
    )
    await rows(
        "events",
        "SELECT to_jsonb(t) FROM execution_events t WHERE owner_scope_key=:scope AND stream_type='run' AND stream_id=ANY(:runs) ORDER BY stream_id,stream_version",
    )
    await rows(
        "audits",
        "SELECT to_jsonb(t) FROM audit_logs t WHERE actor_user_id=:owner AND team_id IS NULL AND resource_id=ANY(:resources) ORDER BY created_at,id",
    )
    # Tasks may embed request bodies; complete task identity, terminal status and
    # request digest suffice for local-send safety. No request/decision contents.
    await rows(
        "tasks",
        "SELECT to_jsonb(t)-'request_payload'-'decision_payload' FROM execution_activity_tasks t WHERE owner_user_id=:owner AND team_id IS NULL AND run_id=ANY(:runs) ORDER BY activity_id",
    )
    await rows(
        "projections",
        "SELECT to_jsonb(t) FROM execution_run_projection t WHERE owner_user_id=:owner AND team_id IS NULL AND run_id::text=ANY(:runs) ORDER BY run_id",
    )
    await rows(
        "reviews",
        "SELECT to_jsonb(t) FROM evaluation_review_commands t WHERE scope_key=:scope AND batch_id=CAST(:batch AS uuid) ORDER BY id",
    )
    await rows(
        "archives",
        "SELECT to_jsonb(t) FROM evaluation_resource_archives t WHERE scope_key=:scope AND kind='batch' AND resource_id=CAST(:batch AS uuid) ORDER BY resource_id",
    )
    parameters["keys"] = sorted(
        {
            bucket["key"]
            for row in tables["reservations"]
            for bucket in row["demand"]["buckets"]
            if bucket["key"].startswith(("5:batch:", "6:purpose:"))
        }
    )
    await rows(
        "buckets",
        "SELECT to_jsonb(t) FROM evaluation_budget_buckets t WHERE key=ANY(:keys) ORDER BY key",
    )
    value = {
        "schema_version": 1,
        "batch_id": batch_id,
        "owner_id": owner_id,
        "batch_scope": parameters["scope"],
        "read_only": (await session.scalar(text("SELECT current_setting('transaction_read_only')")))
        == "on",
        "tables": tables,
    }
    value["source_sha256"] = source_digest()
    try:
        value["verification"] = validate_snapshot(value, batch_id=batch_id, owner_id=owner_id)
    except Exception as error:  # Preserve static validator reason.
        raise ProbeFailure("snapshot-validation", None, error, diagnostic_source=value) from error
    value["source_sha256"] = source_digest()
    return value


async def probe(data, batch_id):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from strict_driver.main import validate_environment
    from strict_driver.ownership import KERNEL

    from app.composition.resources import open_process_resources
    from app.composition.shared import build_shared_services
    from app.composition.tasks import TaskSupervisor
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    UUID(batch_id)
    settings = load_deployment_settings()
    validate_environment(settings, data)
    owner_id = data.bootstrap.operator_id
    scope = OwnerScope.model_validate(data.bootstrap.scope)
    async with open_process_resources(settings, ProcessRole.EXECUTION_KERNEL) as resources:
        supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
        try:
            shared = build_shared_services(resources, supervisor=supervisor)
            async with shared.uow_factory(KERNEL) as work:
                user = await work.user.get_by_id(owner_id)
                if user is None or not user.is_active:
                    raise ValueError("current operator unavailable")
                principal = Principal(
                    user_id=user.id, global_role=user.global_role, token_version=user.token_version
                )
            async with shared.uow_factory(
                AuthorizationContext.for_principal(principal, scope=scope)
            ) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=False)
                await work.evaluation_batch.get(scope, UUID(batch_id))
            engine = create_async_engine(settings.sqlalchemy_database_uri, echo=False)
            try:
                async with AsyncSession(engine) as session:
                    await session.connection(
                        execution_options={
                            "isolation_level": "REPEATABLE READ",
                            "postgresql_readonly": True,
                        }
                    )
                    await configure_session_authorization(
                        session,
                        KERNEL,
                        signing_secret=settings.database_authorization_signing_secret,
                    )
                    await session.execute(
                        text("SELECT set_config('statement_timeout','5000',true)")
                    )
                    value = await snapshot(session, batch_id, owner_id)
                    await session.rollback()
                    return value
            finally:
                await engine.dispose()
        finally:
            await supervisor.stop()


def main():
    from strict_driver.contracts import DriverInput

    body = json.loads(sys.stdin.buffer.read(131073))
    data = DriverInput.model_validate(body["driver"])
    try:
        value = asyncio.run(probe(data, body["batch_id"]))
    except Exception as error:  # noqa: BLE001 - never print SQL/provider private contents
        failed = {
            "error": error.diagnostic if isinstance(error, ProbeFailure) else safe_error(error)
        }
        if isinstance(error, ProbeFailure) and error.diagnostic_source is not None:
            failed.update(
                diagnostic_only=True,
                retention_verified=False,
                diagnostic_source=error.diagnostic_source,
            )
        print(json.dumps(failed, sort_keys=True))
        return 1
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
