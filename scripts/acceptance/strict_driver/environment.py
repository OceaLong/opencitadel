"""Actual broker lifecycle with controlled reset failure and owned child death."""

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from strict_driver.ownership import KERNEL
from strict_driver.recovery import assertion, scenario

from app.application.evaluation.environment_service import EnvironmentWorker
from app.composition.evaluation import build_environment_registry, build_environment_service
from app.domain.evaluation.environment import CaseSlot, EnvironmentOperation


class ResetFault:
    def __init__(self, delegate, lease_id):
        self.delegate, self.lease_id, self.fired = delegate, lease_id, False

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def reset(self, lease, operation, version, targets):
        if lease.id == self.lease_id and not self.fired:
            self.fired = True
            raise RuntimeError("controlled reset boundary failure")
        return await self.delegate.reset(lease, operation, version, targets)


async def pending_for(context, lease_id):
    async with context.shared.uow_factory(KERNEL) as work:
        rows = (
            (
                await work.db_session.execute(
                    text("""
            SELECT id,phase FROM evaluation_environment_operations
            WHERE scope_key=:scope AND lease_id=:lease AND status='queued'
            ORDER BY created_at,id
        """),
                    {"scope": "user:" + context.scope.user_id, "lease": lease_id},
                )
            )
            .mappings()
            .all()
        )
    for row in rows:
        context.own("operation", row["id"])
    return rows


async def lease_of(context, identity):
    async with context.shared.uow_factory(KERNEL) as work:
        return await work.evaluation_environment.lease(context.scope, identity)


async def drain_lease(context, identity, registry):
    worker = EnvironmentWorker(lambda: context.shared.uow_factory(KERNEL), registry)
    for _ in range(5):
        pending = await pending_for(context, identity)
        if not pending:
            return await lease_of(context, identity)
        for row in pending:
            await worker.process(context.scope, row["id"])
    raise RuntimeError("owned environment phases did not converge")


async def repair(context, service, identity, registry):
    async with context.shared.uow_factory(KERNEL) as work:
        await service.cleanup_in_uow(
            work, context.scope, identity, repair=True, principal=context.principal
        )
        await work.commit()
    final = await drain_lease(context, identity, registry)
    assertion("actual_broker_verified_clean", final.state == "verified_clean")
    return final


async def allocate(context, service, repeat):
    identity = uuid4()
    async with context.shared.uow_factory(KERNEL) as work:
        lease = await service.allocate_in_uow(
            work,
            context.scope,
            context.principal,
            context.input.bootstrap.environment.id,
            CaseSlot(
                workspace="user:" + context.scope.user_id,
                batch_id=context.batch_id,
                case_id=context.input.bootstrap.case_id,
                config_version=context.input.bootstrap.configuration_version.id,
                repeat=repeat,
            ),
            lease_id=identity,
        )
        # Write-ahead ownership intent before commit; cleanup tolerates absent IDs.
        context.own("lease", lease.id)
        await work.commit()
    return lease


async def reset_and_worker_death(context):
    registry = build_environment_registry(context.settings)
    service = build_environment_service(
        settings=context.settings,
        resources=context.resources,
        shared=context.shared,
        authorization=KERNEL,
    )
    # First finish the actual cancelled case lease; new physical cases never reuse
    # an old unverified target namespace.
    for kind, identity in list(context.allowed):
        if kind != "lease":
            continue
        async with context.shared.uow_factory(KERNEL) as work:
            await service.cleanup_in_uow(work, context.scope, UUID(identity))
            await work.commit()
        cleaned = await drain_lease(context, UUID(identity), registry)
        assertion("cancelled_case_clean_before_next", cleaned.state == "verified_clean")
    lease = await allocate(context, service, 2)
    adapter = registry.adapters["docker-http-cell-v1"]
    fault = ResetFault(adapter, lease.id)
    registry.adapters["docker-http-cell-v1"] = fault
    quarantined = await drain_lease(context, lease.id, registry)
    registry.adapters["docker-http-cell-v1"] = adapter
    assertions = [
        assertion("real_prepare_then_reset_fault", fault.fired),
        assertion("reset_failure_quarantines", quarantined.state == "quarantine"),
        assertion("same_generation", quarantined.generation == lease.generation),
    ]
    async with context.shared.uow_factory(KERNEL) as work:
        reused = await service.allocate_in_uow(
            work,
            context.scope,
            context.principal,
            context.input.bootstrap.environment.id,
            lease.case_slot,
            lease_id=lease.id,
        )
        assertion("quarantined_namespace_not_reused", reused.state == "quarantine")
        await work.commit()
    from app.application.evaluation.batch_service import BatchService

    reader = BatchService(context.scheduler.suites, preflight_factory=None)
    current = await reader.environments(context.scope, context.principal, context.batch_id)
    quarantined_view = next(row for row in current["items"] if row.id == lease.id)
    assertions.append(
        assertion(
            "authorized_current_quarantine_not_reusable",
            quarantined_view.state == "quarantine" and not quarantined_view.reusable,
        )
    )
    final = await repair(context, service, lease.id, registry)
    current = await reader.environments(context.scope, context.principal, context.batch_id)
    repaired_view = next(row for row in current["items"] if row.id == lease.id)
    assertions.append(
        assertion(
            "authorized_repaired_state_retains_reset_failure",
            repaired_view.state == "verified_clean"
            and repaired_view.reusable
            and repaired_view.prior_failed_operations.get("reset") == 1,
        )
    )
    item = scenario(
        "reset_failure",
        {"batch_id": str(context.batch_id), "lease_id": str(lease.id)},
        {"state": lease.state},
        {
            "quarantine_state": quarantined.state,
            "final_state": final.state,
            "current_environment": repaired_view.model_dump(mode="json"),
        },
        assertions,
        mechanism="reset_boundary_exception_after_real_prepare",
    )
    item["requirement"] = "AC12"
    item["cleanup"] = {"state": "verified_clean", "lease_id": str(lease.id)}
    context.scenarios.append(item)

    victim = await allocate(context, service, 3)
    queued = await pending_for(context, victim.id)
    assertion("one_prepare_operation", len(queued) == 1 and queued[0]["phase"] == "prepare")
    # A test-owned child claims/commits and performs the actual broker operation.
    # Its only stdout is a private parent pipe, never forwarded to browser.
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "strict_driver.worker_child",
        "--input",
        "/acceptance-input.json",
        "--lease",
        str(victim.id),
        "--operation",
        str(queued[0]["id"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1024 * 1024,
        env={**os.environ, "PYTHONPATH": "/acceptance-driver:/app"},
    )
    try:
        raw = await asyncio.wait_for(child.stdout.readline(), timeout=180)
        assertion("child_committed_real_operation", bool(raw) and len(raw) < 1024 * 1024)
        returned = json.loads(raw)
        operation = EnvironmentOperation.model_validate(returned["operation"])
        assertion(
            "exact_child_operation",
            operation.id == queued[0]["id"] and operation.lease_id == victim.id,
        )
        receipt = returned["receipt"]
        assertion("real_prepare_resources", bool(receipt.get("resources")))
        child.kill()
        exit_code = await asyncio.wait_for(child.wait(), timeout=10)
        assertion("actual_child_terminated", exit_code == -9)
    finally:
        if child.returncode is None:
            child.kill()
            await asyncio.wait_for(child.wait(), timeout=10)
    # Genuine process termination AND an explicit deterministic expiry seam.
    # We do not claim that five minutes elapsed in wall-clock time.
    async with context.shared.uow_factory(KERNEL) as work:
        assert (
            await work.evaluation_environment.claim(
                context.scope, operation.id, now=datetime.now(UTC) + timedelta(minutes=6)
            )
            is None
        )
        await work.commit()
    current = await lease_of(context, victim.id)
    assertion("dead_worker_quarantine", current.state == "quarantine")
    try:
        async with context.shared.uow_factory(KERNEL) as work:
            await service.cleanup_in_uow(
                work, context.scope, victim.id, repair=True, principal=context.principal
            )
    except ValueError as error:
        assertion(
            "unknown_operation_blocks_repair",
            str(error) == "environment_repair_unresolved_operation",
        )
    else:
        raise AssertionError("unresolved process death accepted repair")
    async with context.shared.uow_factory(KERNEL) as work:
        accepted = await work.evaluation_environment.complete(context.scope, operation, receipt)
        assertion("late_receipt_not_current_success", accepted is False)
        assertion(
            "receipt_does_not_clear_quarantine",
            (await work.evaluation_environment.lease(context.scope, victim.id)).state
            == "quarantine",
        )
        assertion(
            "exact_receipt_resolves_unknown",
            not await work.evaluation_environment.unresolved_operations(context.scope, victim.id),
        )
        await work.commit()
    # Real physical resources remain bound by deterministic namespace even when
    # the late receipt cannot mutate lease state. Broker cleanup verifies removal.
    final = await repair(context, service, victim.id, registry)
    item = scenario(
        "worker_death",
        {
            "batch_id": str(context.batch_id),
            "lease_id": str(victim.id),
            "operation_id": str(operation.id),
        },
        {"claimed": True, "physical_receipt_sha256": hashlib.sha256(raw).hexdigest()},
        {"quarantined": True, "final_state": final.state, "child_exit_code": exit_code},
        [assertion("new_worker_recovery_verified", final.state == "verified_clean")],
        mechanism="child_process_termination",
    )
    item["requirement"] = "AC12"
    item["fault"]["expiry"] = "explicit_repository_clock_plus_six_minutes"
    item["cleanup"] = {"state": "verified_clean", "lease_id": str(victim.id)}
    context.scenarios.append(item)
