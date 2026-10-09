"""Read-only mounted acceptance launcher; product kernel and dependencies unchanged."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dispatch_audit import AuditLog, observe
from physical_fault_runtime import install
from physical_faults import FaultControl, source_digest


def observer_digest():
    return source_digest()


def main():
    from core.config import load_deployment_settings

    settings = load_deployment_settings()
    project, run_id = os.environ["ACCEPTANCE_PROJECT_ID"], os.environ["ACCEPTANCE_RUN_ID"]
    if (
        settings.env != "test"
        or not settings.evaluation_acceptance_enabled
        or settings.sandbox_labels.get("com.opencitadel.acceptance.project") != project
        or settings.sandbox_labels.get("com.opencitadel.acceptance.run") != run_id
    ):
        raise RuntimeError("observer requires exact owned acceptance deployment")
    control = FaultControl()
    if control.read("arm.json") is not None:
        raise RuntimeError("stale physical fault arm prevents kernel restart")
    path = Path("/tmp/acceptance-dispatch.ndjson")
    if path.is_symlink():
        raise RuntimeError("unsafe observer path")
    path.unlink(missing_ok=True)
    log = AuditLog(path, {"project": project, "run_id": run_id, "source_sha256": observer_digest()})
    from app.application.evaluation.replay_runtime import ReplayRuntime
    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from app.application.execution.agent_tool_catalog import AgentToolCatalog

    for cls, method, kind in (
        (ToolCallActivityHandler, "execute", "handler"),
        (ReplayRuntime, "tool", "replay"),
        (AgentToolCatalog, "invoke", "catalog"),
    ):
        setattr(cls, method, observe(getattr(cls, method), kind, log))
    install(control, log)
    from app.execution_kernel_main import main as kernel_main

    asyncio.run(kernel_main())


if __name__ == "__main__":
    main()
