"""Fixed child entry point, gated by the actual host's repeated inspect readback."""

import asyncio
import json
import os
import sys
from pathlib import Path


async def host_fence():
    print("capacity-fence", flush=True)
    answer = await asyncio.wait_for(asyncio.to_thread(sys.stdin.readline), timeout=90)
    if answer != "capacity-continue\n":
        raise RuntimeError("host containment authorization lost")


async def main():
    await host_fence()  # no settings, clients or database activity before inspect
    from scripts.execution_capacity.host import read_binding, source_digest
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.runtime import historical

    from app.composition.resources import (
        open_process_resources,
    )
    from app.composition.tasks import TaskSupervisor
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    root = Path("/capacity-private")
    binding = read_binding(Path("/capacity-binding.json"))
    if source_digest(Path("/capacity")) != binding["source_sha256"]:
        raise RuntimeError("child mounted source differs")
    settings = load_deployment_settings()
    if settings.env != "test":
        raise RuntimeError("test settings required")
    manifest = json.loads((root / "fixture.json").read_text())
    with RecoveryJournal(root) as journal:
        if journal.parent("binding", binding["invocation"]) != binding:
            raise RuntimeError("private binding mismatch")
        attempt = journal.parent("attempt", os.environ["CAPACITY_ATTEMPT_ID"])
        if attempt != {
            "invocation": binding["invocation"],
            "fixture_id": binding["fixture_id"],
            "source_sha256": binding["source_sha256"],
        }:
            raise RuntimeError("actual host attempt binding differs")
        from scripts.execution_capacity.composition import verified_factories

        if (
            settings.storage_provider != "minio"
            or settings.minio_endpoint != binding["minio_endpoint"]
            or settings.minio_bucket != binding["minio_bucket"]
        ):
            raise RuntimeError("actual MinIO endpoint/bucket binding differs")
        factories = verified_factories(settings, binding)
        async with open_process_resources(
            settings, ProcessRole.EXECUTION_KERNEL, factories=factories
        ) as resources:
            supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
            try:
                from scripts.execution_capacity.batch_runtime import construct_batch
                from scripts.execution_capacity.probe import construct_probe

                parts = {}
                for phase, build, expected in (
                    ("historical", historical, "historical_ready"),
                    ("step_probe", construct_probe, "probe_ready"),
                    ("batch", construct_batch, "batch_ready"),
                ):
                    journal.intent(
                        "phase",
                        phase,
                        {
                            "fixture_id": binding["fixture_id"],
                            "source_sha256": binding["source_sha256"],
                        },
                    )
                    prior = journal.get("phase", phase)
                    if prior["receipt"] is not None and phase != "batch":
                        # Later batch baseline verifies all retained Runs belong
                        # to these exact sealed historical/probe journal parents.
                        parts[phase] = prior["receipt"]
                        continue
                    current = await build(
                        resources, supervisor, journal, binding, manifest, host_fence=host_fence
                    )
                    if current.get("status") != expected or not current.get("converged"):
                        raise RuntimeError("capacity phase did not converge: " + phase)
                    if prior["receipt"] is None:
                        journal.acknowledge("phase", phase, current)
                    parts[phase] = current
                result = {
                    "status": "corpus_ready",
                    "converged": True,
                    "fixture_complete": False,
                    "parts": parts,
                }
                from scripts.execution_capacity.seal_handoff import child_handoff

                await child_handoff(binding, root)
            finally:
                reports = await supervisor.stop()
                resources.object_storage_client.record.supervisor(reports)
        result["fixture_id"] = binding["fixture_id"]
        result["source_sha256"] = binding["source_sha256"]
        result["attempt_id"] = os.environ["CAPACITY_ATTEMPT_ID"]
        from scripts.execution_capacity.ownership import _open_private

        fd = _open_private(root / "historical-result.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream)
            stream.flush()
            os.fsync(stream.fileno())


if __name__ == "__main__":
    asyncio.run(main())
