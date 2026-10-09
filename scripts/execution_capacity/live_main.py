"""Explicit owned guest source-window driver; not the capacity benchmark (C)."""

import argparse
import asyncio
import time
from pathlib import Path
from uuid import UUID


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--window-id",
        type=UUID,
        required=True,
        help="Exact preregistered binding.live.windows key; single use",
    )
    return result


async def run(window_id):
    from scripts.execution_capacity.composition import verified_factories
    from scripts.execution_capacity.host import read_binding, source_digest
    from scripts.execution_capacity.live_runtime import LiveWorkload
    from scripts.execution_capacity.observers import RecoveryJournal

    from app.composition.resources import open_process_resources
    from app.composition.shared import build_shared_services
    from app.composition.tasks import TaskSupervisor
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    binding = read_binding(Path("/capacity-binding.json"))
    if source_digest(Path("/capacity")) != binding["source_sha256"]:
        raise ValueError("source binding differs")
    settings = load_deployment_settings()
    with RecoveryJournal(Path("/capacity-live")) as journal:
        async with open_process_resources(
            settings, ProcessRole.API, factories=verified_factories(settings, binding)
        ) as resources:
            supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
            primary = None
            try:
                shared = build_shared_services(resources, supervisor=supervisor)
                await shared.runtime_policy_reader.initialize()
                ready = time.monotonic_ns()
                workload = LiveWorkload(resources, shared, journal, binding)
                async with workload.window(window_id, minimal_ready_ns=ready):
                    # Source-only window leaves native collection pending. C
                    # enters the same context and performs its actual targets.
                    pass
            except BaseException as failure:
                primary = failure
                raise
            finally:
                try:
                    reports = await supervisor.stop()
                    resources.object_storage_client.record.supervisor(reports)
                except BaseException as shutdown:
                    if primary is not None:
                        raise BaseExceptionGroup(
                            "live driver and supervisor shutdown failed", [primary, shutdown]
                        ) from None
                    raise


def main():
    args = parser().parse_args()
    asyncio.run(run(args.window_id))


if __name__ == "__main__":
    main()
