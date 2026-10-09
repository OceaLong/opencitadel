"""Bounded owned worker victim; stdout is a private pipe to its driver parent."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from strict_driver.contracts import DriverInput
from strict_driver.main import validate_environment
from strict_driver.ownership import KERNEL

from app.composition.evaluation import build_environment_registry
from app.composition.resources import open_process_resources
from app.composition.shared import build_shared_services
from app.composition.tasks import TaskSupervisor
from app.runtime_role import ProcessRole
from core.config import load_deployment_settings


async def victim(data, lease_id, operation_id):
    from app.domain.models.scope import OwnerScope

    settings = load_deployment_settings()
    validate_environment(settings, data)
    scope = OwnerScope.model_validate(data.bootstrap.scope)
    async with open_process_resources(settings, ProcessRole.EXECUTION_KERNEL) as resources:
        supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
        try:
            shared = build_shared_services(resources, supervisor=supervisor)
            async with shared.uow_factory(KERNEL) as work:
                lease = await work.evaluation_environment.lease(scope, lease_id)
                if (
                    lease.environment_version != data.bootstrap.environment.id
                    or lease.case_slot.case_id != data.bootstrap.case_id
                ):
                    raise RuntimeError("foreign victim lease")
                claimed = await work.evaluation_environment.claim(scope, operation_id)
                if claimed is None or claimed[0].lease_id != lease_id:
                    raise RuntimeError("victim operation unavailable")
                operation, lease = claimed
                await work.commit()
            async with shared.uow_factory(KERNEL) as work:
                version = await work.evaluation_environment.registered(
                    scope, "environment", lease.environment_version
                )
                targets = tuple(
                    [
                        await work.evaluation_environment.registered(
                            scope, "target", ref.id, ref.revision
                        )
                        for ref in version.allowed_targets
                    ]
                )
            adapter = build_environment_registry(settings).resolve(version, targets)
            receipt = await getattr(
                adapter, "verify" if operation.phase.startswith("verify") else operation.phase
            )(lease, operation, version, targets)
            print(
                json.dumps({"operation": operation.model_dump(mode="json"), "receipt": receipt}),
                flush=True,
            )
            # Parent must terminate this exact child. Timeout is failure, never success.
            await asyncio.wait_for(asyncio.Event().wait(), timeout=30)
        finally:
            await supervisor.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--lease", type=UUID, required=True)
    parser.add_argument("--operation", type=UUID, required=True)
    args = parser.parse_args()
    asyncio.run(
        victim(
            DriverInput.model_validate_json(Path(args.input).read_bytes()),
            args.lease,
            args.operation,
        )
    )


if __name__ == "__main__":
    main()
