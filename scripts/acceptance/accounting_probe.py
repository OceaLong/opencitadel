"""Mounted read-only collector for one already terminal public acceptance batch."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parent))


async def probe(data, batch_id):
    from accounting_retention import validate_accounting
    from sqlalchemy import text
    from strict_driver.main import validate_environment
    from strict_driver.ownership import KERNEL

    from app.composition.resources import open_process_resources
    from app.composition.shared import build_shared_services
    from app.composition.tasks import TaskSupervisor
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.repositories.db_evaluation_batch_repository import effect_query
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    UUID(batch_id)
    settings = load_deployment_settings()
    validate_environment(settings, data)
    async with open_process_resources(settings, ProcessRole.EXECUTION_KERNEL) as resources:
        supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
        try:
            shared = build_shared_services(resources, supervisor=supervisor)
            async with shared.uow_factory(KERNEL) as work:
                # The host runner already verifies the migration head and binds
                # this exact kernel image. Its ordinary role cannot read the
                # administrative Alembic table; retain normal user authority here.
                user = await work.user.get_by_id(data.bootstrap.operator_id)
                if user is None or not user.is_active:
                    raise ValueError("operator unavailable")
                principal = Principal(
                    user_id=user.id, global_role=user.global_role, token_version=user.token_version
                )
            scope = OwnerScope.model_validate(data.bootstrap.scope)
            async with shared.uow_factory(
                AuthorizationContext.for_principal(principal, scope=scope)
            ) as work:
                await work.evaluation_dataset.authorize(scope, principal, write=False)
                batch = await work.evaluation_batch.get(scope, UUID(batch_id))
            async with shared.uow_factory(KERNEL) as work:
                parameters = {"scope": "user:" + principal.user_id, "batch": batch_id}
                rows = (
                    (
                        await work.db_session.execute(
                            text("""
                    SELECT d.call_identity,d.run_id::text,d.activity_id::text,c.purpose,
                           r.state,r.settled_at,r.demand,r.settlement,s.fact,c.body->'price' AS price
                    FROM execution_model_dispatches d
                    JOIN execution_configurations c ON c.id=d.configuration_id AND c.scope_key=d.scope_key
                    JOIN evaluation_budget_reservations r ON r.call_identity::text=d.call_identity AND r.scope_key=d.scope_key
                    LEFT JOIN execution_model_settlements s ON s.call_identity=d.call_identity AND s.scope_key=d.scope_key
                    WHERE d.scope_key=:scope AND r.demand->>'batch_id'=:batch
                    ORDER BY d.call_identity
                """),
                            parameters,
                        )
                    )
                    .mappings()
                    .all()
                )
                calls = [dict(row) for row in rows]
                # Immutable facts only. Request snapshots, payloads and credentials are never selected.
                pending = await work.db_session.scalar(
                    text(f"""
                    SELECT count(*) FROM evaluation_batch_results result
                    JOIN evaluation_batch_attempts attempt ON attempt.scope_key=result.scope_key
                      AND attempt.result_id=result.id AND attempt.attempt=result.attempt
                    WHERE result.scope_key=:scope AND result.batch_id=CAST(:batch AS uuid)
                      AND (result.unknown_effect OR result.recovery_pending OR ({effect_query("result.scope_key", "attempt.run_id", "true")}))
                """),
                    parameters,
                )
                keys = sorted(
                    {
                        bucket["key"]
                        for row in calls
                        for bucket in row["demand"]["buckets"]
                        if bucket["key"].startswith("6:purpose:" + batch_id + ":")
                    }
                )
                buckets = (
                    (
                        await work.db_session.execute(
                            text("""
                    SELECT key,slots,reserved_tokens,reserved_money FROM evaluation_budget_buckets
                    WHERE key=ANY(:keys) ORDER BY key
                """),
                            {"keys": keys},
                        )
                    )
                    .mappings()
                    .all()
                )
                leases = (
                    (
                        await work.db_session.execute(
                            text("""
                    SELECT id::text,state FROM evaluation_environment_leases
                    WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch ORDER BY id
                """),
                            parameters,
                        )
                    )
                    .mappings()
                    .all()
                )
                value = {
                    "environment_leases": [dict(row) for row in leases],
                    "batch_id": batch_id,
                    "owner_id": principal.user_id,
                    "batch_status": batch["status"],
                    "cleanup_status": batch["cleanup_status"],
                    "pending_effects": pending,
                    "unsettled_calls": sum(
                        row["state"] != "settled" or row["fact"] is None for row in calls
                    ),
                    "calls": calls,
                    "buckets": [dict(row) for row in buckets],
                }
                value = json.loads(json.dumps(value, default=str))
                value["retention"] = validate_accounting(
                    value, batch_id=batch_id, owner_id=principal.user_id
                )
                value["source_sha256"] = source_digest()
                return value
        finally:
            await supervisor.stop()


def source_digest():
    return hashlib.sha256(
        b"".join(
            (Path(__file__).parent / name).read_bytes()
            for name in ("accounting_probe.py", "accounting_retention.py")
        )
    ).hexdigest()


def main():
    from strict_driver.contracts import DriverInput

    body = json.loads(sys.stdin.buffer.read(131073))
    data = DriverInput.model_validate(body["driver"])
    try:
        result = asyncio.run(probe(data, body["batch_id"]))
    except Exception as error:  # noqa: BLE001 - never emit private SQL or provider payloads
        print(json.dumps({"error": type(error).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
