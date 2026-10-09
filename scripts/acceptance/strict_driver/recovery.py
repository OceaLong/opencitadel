"""Real application/repository recovery seams. No pytest, fake receipts or SQL writes."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from strict_driver.ownership import KERNEL, assert_exclusive

from app.application.evaluation.batch_service import BatchService
from app.composition.evaluation import build_batch_scheduler


def assertion(identity, condition):
    if not condition:
        raise AssertionError(identity)
    return {"id": identity, "passed": True}


def scenario(identity, resources, before, after, assertions, *, mechanism="application_commands"):
    return {
        "requirement": "AC13",
        "id": identity,
        "status": "passed",
        "test_id": "strict_driver.recovery." + identity,
        "resource_ids": resources,
        "before": before,
        "after": after,
        "assertions": assertions,
        "fault": {"mechanism": mechanism},
        "artifact": "strict-raw.json",
        "cleanup": {
            "state": "pending",
            "obligations": ["cancel batch through public API", "archive retained batch history"],
        },
    }


async def submit_and_fence(context):
    """Concurrent idempotent user commands, then explicit repository clock fencing."""
    import asyncio

    scheduler = context.scheduler
    scope, principal = context.scope, context.principal
    service = BatchService(scheduler.suites, preflight_factory=scheduler.preflight_factory)
    check = await scheduler.preflight_factory(principal).check(
        scope, context.input.bootstrap.suite_version.id
    )
    assertion("fresh_preflight_allowed", check.allowed)
    payload = {
        "suite_version": str(context.input.bootstrap.suite_version.id),
        "preflight_revision": check.revision,
    }
    key = str(uuid4())
    context.own("batch_request", key)

    async def submit():
        result = await service.start(scope, principal, key, payload)
        # Record each successful response before the next fallible action.
        context.own("batch", result.id)
        return result

    first, second = await asyncio.gather(submit(), submit())
    context.batch_id = first.id
    assertions = [assertion("one_batch_identity", first.id == second.id)]
    context.scenarios.append(
        scenario(
            "concurrent_submit",
            {"batch_id": str(first.id)},
            {"request_id": key, "preflight_revision": check.revision},
            {"first_id": str(first.id), "second_id": str(second.id)},
            assertions,
        )
    )
    await assert_exclusive(context.shared.uow_factory, context.allowed)
    now = datetime.now(UTC)
    async with context.shared.uow_factory(KERNEL) as work:
        original = await work.evaluation_batch.claim(now, lease_seconds=1)
        assertion("only_owned_batch_claimed", original is not None and original["id"] == first.id)
        await work.commit()
    async with context.shared.uow_factory(KERNEL) as work:
        replacement = await work.evaluation_batch.claim(now + timedelta(seconds=2))
        assertion("replacement_is_owned", replacement is not None and replacement["id"] == first.id)
        await work.commit()
    # A rejected renew must not poison the transaction used for the valid renew.
    try:
        async with context.shared.uow_factory(KERNEL) as work:
            await work.evaluation_batch.renew(original)
    except ValueError as exc:
        assertion("stale_renew_claim_lost", str(exc) == "claim_lost")
    else:
        raise AssertionError("expired owner renewed")
    async with context.shared.uow_factory(KERNEL) as work:
        await work.evaluation_batch.release(original)
        current = await work.evaluation_batch.get(scope, first.id)
        assertions = [
            assertion("generation_increased", replacement["generation"] > original["generation"]),
            assertion(
                "stale_release_did_not_clear_replacement", current["claim_until"] is not None
            ),
        ]
        await work.evaluation_batch.renew(replacement)
        await work.evaluation_batch.release(replacement)
        await work.commit()
    context.scenarios.append(
        scenario(
            "lease_transfer",
            {"batch_id": str(first.id)},
            {"generation": original["generation"]},
            {"generation": replacement["generation"]},
            assertions,
            mechanism="explicit_repository_clock",
        )
    )
    return service


async def admission_restart(context):
    """Real admission commit with one discarded acknowledgement, fresh scheduler."""
    from app.application.evaluation.environment_service import EnvironmentWorker
    from app.composition.evaluation import build_environment_registry

    original = context.scheduler.admission.admit
    fired = False

    async def lose_ack(**kwargs):
        nonlocal fired
        answer = await original(**kwargs)
        if not fired:
            fired = True
            raise ConnectionError("owned admission commit acknowledgement withheld")
        return answer

    context.scheduler.admission.admit = lose_ack
    worker = EnvironmentWorker(
        lambda: context.shared.uow_factory(KERNEL), build_environment_registry(context.settings)
    )
    try:
        for _ in range(12):
            await assert_exclusive(context.shared.uow_factory, context.allowed)
            try:
                await context.scheduler.tick(datetime.now(UTC))
            except ConnectionError:
                break
            # Scheduler allocates actual case leases. Record only leases belonging
            # to the exact batch before processing any pending operation.
            await context.register_batch_children()
            async with context.shared.uow_factory(KERNEL) as work:
                pending = await work.evaluation_environment.pending(limit=100)
            for row in pending:
                await context.assert_owned_operation(row["id"])
                await worker.process(context.scope, row["id"])
        assertion("admission_boundary_reached", fired)
    finally:
        context.scheduler.admission.admit = original
    await context.register_batch_children()
    async with context.shared.uow_factory(KERNEL) as work:
        rows = await work.evaluation_batch.results(context.scope, context.batch_id)
        prepared = [row for row in rows if row["prepared_envelope"]]
        assertion("one_prepared_case", len(prepared) == 1)
        frozen = prepared[0]["prepared_envelope"]
        assertion(
            "crashed_scheduler_released_claim",
            (await work.evaluation_batch.get(context.scope, context.batch_id))["claim_until"]
            is None,
        )
    restarted = build_batch_scheduler(
        settings=context.settings, resources=context.resources, shared=context.shared
    )
    # Shared admission object restored before constructing a new scheduler.
    await restarted.tick(datetime.now(UTC))
    await restarted.tick(datetime.now(UTC))
    await context.register_batch_children()
    async with context.shared.uow_factory(KERNEL) as work:
        rows = await work.evaluation_batch.results(context.scope, context.batch_id)
        row = next(row for row in rows if row["id"] == prepared[0]["id"])
        assertions = [
            assertion("same_prepared_envelope", row["envelope"] == frozen),
            assertion(
                "one_case_run", len({str(item["run_id"]) for item in rows if item["run_id"]}) == 1
            ),
        ]
    authorized = await BatchService(
        restarted.suites, preflight_factory=restarted.preflight_factory
    ).results(context.scope, context.principal, context.batch_id)
    assertions.append(
        assertion(
            "current_authorized_result_identity", str(authorized["items"][0].id) == str(row["id"])
        )
    )
    context.scenarios.append(
        scenario(
            "admission_restart",
            {
                "batch_id": str(context.batch_id),
                "result_id": str(row["id"]),
                "run_id": str(row["run_id"]),
            },
            {"prepared": True, "claim_released": True},
            {"authorized_result_id": str(authorized["items"][0].id), "same_envelope": True},
            assertions,
            mechanism="boundary_exception_and_new_scheduler",
        )
    )
