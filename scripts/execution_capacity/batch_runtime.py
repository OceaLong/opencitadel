"""Real request-local publication alongside the complete normal kernel lifecycle."""

import asyncio
from contextlib import asynccontextmanager


async def with_kernel(settings, factory, observe, *, kernel=None):
    """Keep normal run_kernel supervision/signals; request its own graceful stop."""
    if kernel is None:
        from app.execution_kernel_main import run_kernel

        kernel = run_kernel
    ready = asyncio.Event()
    actual = []

    @asynccontextmanager
    async def capture(resolved):
        async with factory(resolved) as runtime:
            actual.append(runtime)
            ready.set()
            yield runtime

    task = asyncio.create_task(kernel(settings, runtime_factory=capture))
    waiter = asyncio.create_task(ready.wait())
    observation = None
    primary = None
    try:
        done, _ = await asyncio.wait((task, waiter), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            raise RuntimeError("kernel stopped before publication")
        observation = asyncio.create_task(observe())
        done, _ = await asyncio.wait((task, observation), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            raise RuntimeError("kernel stopped before batch convergence")
        return await observation
    except BaseException as error:
        primary = error
        raise
    finally:
        if observation is not None and not observation.done():
            observation.cancel()
            await asyncio.gather(observation, return_exceptions=True)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        if actual:
            actual[0].supervisor.request_stop()
        elif not task.done():
            task.cancel()  # startup has not yielded a supervisor; close its context
        # Normal runtime context owns its configured shutdown deadline and
        # shielded storage observer is drained by the caller after this exits.
        try:
            await task
        except BaseException as shutdown:
            if primary is not None and shutdown is not primary:
                raise BaseExceptionGroup(
                    "batch observation and kernel shutdown failed", [primary, shutdown]
                ) from None
            raise


async def construct_batch(resources, supervisor, journal, binding, manifest, *, host_fence):
    from datetime import UTC, datetime
    from uuid import uuid5

    from scripts.execution_capacity.batch import (
        Operations,
        publish_corpus,
        result_inventory,
        start_batch,
    )
    from scripts.execution_capacity.batch_facts import BatchFacts, BatchStorage
    from scripts.execution_capacity.composition import runtime_factories
    from scripts.execution_capacity.persistence import PersistedFacts
    from scripts.execution_capacity.runtime import KERNEL, verify_prerequisite

    from app.application.evaluation.batch_service import BatchService
    from app.application.security.authorization_context import authorization_scope
    from app.composition.evaluation import build_batch_scheduler, build_environment_service
    from app.composition.shared import build_shared_services
    from app.domain.evaluation.batch import TERMINAL_BATCH
    from app.domain.models.scope import OwnerScope, Principal
    from app.execution_kernel_main import start_kernel_metrics_server
    from app.infrastructure.logging import setup_logging
    from app.observability.otel import setup_observability

    settings = resources.settings
    shared = build_shared_services(resources, supervisor=supervisor)
    await shared.runtime_policy_reader.initialize()
    scope = OwnerScope.personal(binding["principal_id"])
    facts = PersistedFacts(resources.postgres.session_factory, KERNEL, journal, scope, host_fence)
    authorized, _ = await verify_prerequisite(shared, facts, binding)
    # Current persisted authenticated principal, not a caller-supplied role.
    async with shared.uow_factory(KERNEL) as work:
        user = await work.user.get_by_id(scope.user_id)
        principal = Principal(
            user_id=user.id, global_role=user.global_role, token_version=user.token_version
        )
    operations = Operations(journal, binding["fixture_id"], facts.scope_key, principal.user_id)
    scheduler = build_batch_scheduler(settings=settings, resources=resources, shared=shared)
    service = BatchService(scheduler.suites, preflight_factory=scheduler.preflight_factory)
    environments = build_environment_service(
        settings=settings, resources=resources, shared=shared, authorization=authorized
    )
    # DatasetObjectLifecycle performs durable exact object registration before
    # storage; this request-local graph intentionally retains that implementation.
    with authorization_scope(authorized):
        for selection in [*binding["batch"]["subjects"], binding["batch"]["judge"]]:
            resolved = await shared.inference_model_service.resolve_chat(
                selection["model_id"], scope=scope
            )
            if (
                resolved.model_name != "acceptance-capacity"
                or resolved.base_url != binding["provider_endpoint"]
            ):
                raise ValueError("batch model is not the exact owned fixed100ms capacity provider")
        dataset, configs, judge, rubric, suite = await publish_corpus(
            scheduler.suites.datasets,
            scheduler.suites,
            environments,
            scope,
            principal,
            operations,
            binding["batch"],
            settings,
        )
        batch = await start_batch(service, scope, principal, operations, suite)
    journal.intent(
        "batch",
        batch.id,
        {
            "scope": facts.scope_key,
            "suite_id": str(suite.id),
            "dataset_id": str(dataset.id),
            "dataset_entity_id": str(dataset.dataset_id),
            "config_ids": [str(c.id) for c in configs],
            "judge_id": str(judge.id),
            "rubric_id": str(rubric.id),
        },
    )
    actual = BatchFacts(
        resources.postgres.session_factory, KERNEL, journal, scope, host_fence, batch_id=batch.id
    )
    await actual.assert_owned()  # reject foreign work before any normal worker starts
    observed = []

    def wrapper(real):
        value = BatchStorage(
            real, journal, actual, queue_limit=settings.execution_activity_max_concurrency + 110
        )
        observed.append(value)
        return value

    factories = runtime_factories(settings, binding, object_storage_wrapper=wrapper)

    async def completion():
        deadline = (
            asyncio.get_running_loop().time()
            + suite.settings.batch_timeout_seconds
            + settings.shutdown_timeout_seconds
        )
        while True:
            _, physical_clean = await actual.assert_owned()
            current = await service.get(scope, principal, batch.id)
            if (
                current.status in TERMINAL_BATCH
                and current.cleanup_status == "clean"
                and physical_clean
            ):
                inventory = await result_inventory(
                    service,
                    scope,
                    principal,
                    batch.id,
                    [c.id for c in dataset.cases],
                    [c.id for c in configs],
                )
                journal.acknowledge(
                    "batch",
                    batch.id,
                    {
                        "status": current.status,
                        "cleanup_status": current.cleanup_status,
                        **inventory,
                    },
                )
                return {
                    "status": "batch_ready" if current.status == "completed" else "failed",
                    "batch_id": str(batch.id),
                    "suite_id": str(suite.id),
                    "dataset_id": str(dataset.id),
                    "config_ids": [str(c.id) for c in configs],
                    "judge_id": str(judge.id),
                    "rubric_id": str(rubric.id),
                    "version_unpinned": {
                        str(c.id): {
                            "version_unpinned": c.version_unpinned,
                            "unpinned_reasons": list(c.unpinned_reasons),
                        }
                        for c in [*configs, judge]
                    },
                    "cleanup_status": current.cleanup_status,
                    "fixture_complete": False,
                    **inventory,
                }
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("actual batch terminal/clean deadline exceeded")
            await asyncio.sleep(1)

    async def observe():
        with authorization_scope(authorized):
            try:
                return await completion()
            except Exception as failure:
                journal.intent(
                    "batch_failure",
                    str(uuid5(batch.id, "failure")),
                    {"batch_id": str(batch.id), "error_type": type(failure).__name__},
                )
                # Ordinary cancel preserves extra facts and unknown effects. A
                # cancellation response is not a physical cleanup certificate.
                request = str(uuid5(batch.id, "capacity:cancel"))
                await service.cancel(scope, principal, request, {"batch_id": str(batch.id)})
                await actual.environments()
                raise

    setup_logging(settings)
    setup_observability(settings=settings)
    start_kernel_metrics_server(settings)
    failure = None
    try:
        result = await with_kernel(settings, factories.kernel, observe)
    except BaseException as error:
        failure = error
        raise
    finally:
        errors = []
        for storage in observed:
            try:
                await storage.drain()
            except BaseException as error:  # noqa: BLE001 - preserve primary and drain/cancellation failures
                errors.append(error)
        # Capture late/unknown operations again after complete kernel shutdown.
        try:
            await actual.environments(final=True)
        except BaseException as error:  # noqa: BLE001 - retain recovery failure alongside primary
            errors.append(error)
        if errors:
            raise BaseExceptionGroup(
                "batch shutdown/recovery failures", ([failure] if failure else []) + errors
            )
    _, clean = await actual.assert_owned(final=True)
    if not clean:
        raise RuntimeError("broker operation convergence unverified; restoration withheld")
    result["converged"] = True
    result["completed_at"] = datetime.now(UTC).isoformat()
    return result
