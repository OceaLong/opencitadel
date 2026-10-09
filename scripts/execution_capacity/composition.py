"""Trusted capacity composition; normal API/kernel lifecycle and defaults remain intact.

The private binding is an operator prerequisite, not self-attested containment.
The host must corroborate container/image/argv/mount ownership before launch.
"""

import re
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from scripts.execution_capacity.writer_storage import ObservedMinio

from app.composition.api import open_api_runtime
from app.composition.kernel import open_kernel_runtime
from app.composition.resources import DEFAULT_RESOURCE_FACTORIES, ResourceFactories
from app.infrastructure.execution.query_observation import install_query_observation
from app.infrastructure.storage.postgres import Postgres


def verified_factories(settings, binding, *, writer_root=Path("/capacity-writers")):
    def validate(actual):
        if (
            actual.env != "test"
            or binding["environment"] != "test"
            or actual.storage_provider != "minio"
            or actual.minio_endpoint != binding["minio_endpoint"]
            or actual.minio_bucket != binding["minio_bucket"]
            or not actual.minio_endpoint
            or not actual.minio_bucket
            or not re.fullmatch(r"[0-9a-f]{64}", binding["source_sha256"])
        ):
            raise ValueError("capacity storage target binding differs")
        UUID(binding["invocation"])
        UUID(binding["fixture_id"])

    validate(settings)

    def storage(actual):
        validate(actual)
        value = ObservedMinio(actual, root=writer_root, writer_id=str(uuid4()), binding=binding)
        storage.instances.append(value)
        return value

    storage.instances = []
    return ResourceFactories(
        postgres=lambda actual: Postgres(actual, engine_observer=install_query_observation),
        redis=DEFAULT_RESOURCE_FACTORIES.redis,
        storage=storage,
    )


@dataclass(frozen=True)
class RuntimeFactories:
    api: object
    kernel: object


def runtime_factories(
    settings, binding, *, object_storage_wrapper=None, writer_root=Path("/capacity-writers")
):
    factories = verified_factories(settings, binding, writer_root=writer_root)

    def shutdown(reports):
        for storage in factories.storage.instances:
            if storage.record is not None:
                storage.record.supervisor(reports)

    @asynccontextmanager
    async def api(actual, *, on_critical_failure=None):
        verified_factories(actual, binding)
        async with open_api_runtime(
            actual,
            factories=factories,
            on_critical_failure=on_critical_failure,
            shutdown_observer=shutdown,
        ) as runtime:
            yield runtime

    @asynccontextmanager
    async def kernel(actual, *, on_critical_failure=None):
        verified_factories(actual, binding)
        with ExitStack() as stack:
            from scripts.execution_capacity.broker_inventory import BrokerRequests
            from scripts.execution_capacity.observers import RecoveryJournal

            broker_journal = stack.enter_context(RecoveryJournal(writer_root))
            options = {}
            live = binding.get("live")
            if live is not None:
                import os
                import socket
                import time
                from uuid import uuid4

                from scripts.execution_capacity.live import ObservedProgress, validate_topology
                from scripts.execution_capacity.observers import RecoveryJournal

                if live["profile"] != "finite-text-120x500ms-v1":
                    raise ValueError("unrecognized live profile")
                topology = validate_topology(actual, live["workers"])
                journal = stack.enter_context(RecoveryJournal(Path("/capacity-live")))
                boot = ObservedProgress(None, None).boot_id
                identity = str(uuid4())
                journal.intent(
                    "live_worker",
                    identity,
                    {
                        "hostname": socket.gethostname(),
                        "pid": os.getpid(),
                        "boot_id": boot,
                        "started_ns": time.monotonic_ns(),
                        "source_sha256": binding["source_sha256"],
                        "topology": topology,
                        "profile": live["profile"],
                    },
                )
                options = {
                    "text_stream": True,
                    "progress_sink_wrapper": lambda sink: ObservedProgress(
                        sink,
                        journal,
                        boot_id=boot,
                        marker_root=Path("/capacity-live/bridge")
                        if live.get("bridge") is True
                        else None,
                    ),
                }
            async with open_kernel_runtime(
                actual,
                factories=factories,
                on_critical_failure=on_critical_failure,
                object_storage_wrapper=object_storage_wrapper,
                broker_request_observer=BrokerRequests(broker_journal),
                shutdown_observer=shutdown,
                **options,
            ) as runtime:
                yield runtime
            if live is not None:
                journal.acknowledge("live_worker", identity, {"stopped_ns": time.monotonic_ns()})

    return RuntimeFactories(api, kernel)


def load_trusted_runtime():
    """Fixed read-only mount paths; never a public API flag or arbitrary hook."""
    from scripts.execution_capacity.host import read_binding, source_digest

    from core.config import load_deployment_settings

    binding = read_binding(Path("/capacity-binding.json"))
    if source_digest(Path("/capacity")) != binding["source_sha256"]:
        raise ValueError("capacity mounted source binding differs")
    settings = load_deployment_settings()
    return settings, runtime_factories(settings, binding)
