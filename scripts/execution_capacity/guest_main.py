"""Fixed in-container helper. Import/help performs no resource access.

cold-window is a long-lived operation; status/stamp/client-ready/abort/result
operate on the exact same boot/window nonce. Native facts come only from C3.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import UUID

from scripts.execution_capacity.reference_protocol import Action


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "action", choices=[a.value for a in Action if a not in (Action.INFRASTRUCTURE, Action.SEAL)]
    )
    result.add_argument("request", help="fixed bounded JSON identity and action fields")
    return result


def open_state(request):
    from scripts.execution_capacity.guest_state import GuestState
    from scripts.execution_capacity.host import read_binding, source_digest

    identity = request["identity"]
    binding = read_binding(Path("/capacity-binding.json"))
    if binding["live"].get("bridge") is not True:
        raise ValueError("benchmark bridge not provisioned in sealed binding")
    if (
        identity["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        or identity["source_digest"] != binding["source_sha256"]
        or source_digest(Path("/capacity")) != binding["source_sha256"]
    ):
        raise ValueError("guest boot/source identity differs")
    if identity["window_id"] not in binding["live"]["windows"]:
        raise ValueError("window absent from sealed binding")
    return GuestState(Path("/capacity-live/bridge"), identity), binding


async def cold_window(state, binding):
    import fcntl
    import os

    from scripts.execution_capacity.composition import verified_factories
    from scripts.execution_capacity.live_runtime import LiveWorkload
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.ownership import _open_private

    from app.composition.resources import open_process_resources
    from app.composition.shared import build_shared_services
    from app.composition.tasks import TaskSupervisor
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    lock = _open_private(state.root / "window.lock", os.O_RDWR | os.O_CREAT)
    primary = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state.initialize()
        settings = load_deployment_settings()
        with RecoveryJournal(Path("/capacity-live")) as journal:
            async with open_process_resources(
                settings, ProcessRole.API, factories=verified_factories(settings, binding)
            ) as resources:
                supervisor = TaskSupervisor(
                    shutdown_timeout_seconds=settings.shutdown_timeout_seconds
                )
                try:
                    shared = build_shared_services(resources, supervisor=supervisor)
                    await shared.runtime_policy_reader.initialize()
                    ready = time.monotonic_ns()
                    state.publish("minimal_ready", {"guest_ns": ready})
                    workload = LiveWorkload(resources, shared, journal, binding, bridge=state)
                    async with workload.window(UUID(state.key), minimal_ready_ns=ready) as window:
                        state.publish("running", window)
                        while state.read("client_done") is None:
                            if state.read("abort") is not None:
                                raise RuntimeError("host requested exact owned window abort")
                            if time.monotonic_ns() >= window["end_ns"]:
                                raise TimeoutError("client-done missed original fixed deadline")
                            await asyncio.sleep(0.01)
                        state.require_done()
                    receipt = journal.get("live_window", state.key)
                    state.publish("source_result", receipt)
                except BaseException as failure:
                    primary = failure
                    state.publish("failure", {"type": type(failure).__name__})
                    raise
                finally:
                    try:
                        reports = await supervisor.stop()
                        resources.object_storage_client.record.supervisor(reports)
                    except BaseException as shutdown:
                        state.publish("shutdown_failure", {"type": type(shutdown).__name__})
                        if primary is not None:
                            raise BaseExceptionGroup(
                                "source and shutdown failed", [primary, shutdown]
                            ) from None
                        raise
        state.publish(
            "source_settled",
            {"guest_ns": time.monotonic_ns(), "host_physical_cleanup": "pending_C"},
        )
    finally:
        os.close(lock)


async def collect_inventory(reader, *, phase, state=None, base=()):
    """Guest coordinator boundary for C2c pre-seal/post-round reads.

    The C2c shutdown coordinator supplies already verified resources/journals to
    SourceInventoryReader. This is deliberately absent from cold readiness and
    never opens services, creates parents, starts workers or grants seal/reuse.
    """
    if phase not in {"pre_seal", "post_round"}:
        raise ValueError("inventory phase cannot warm a cold timed target")
    if phase == "pre_seal":
        if reader.origin.kind != "base" or state is not None or base:
            raise ValueError("immutable pre-seal source origin required")
    else:
        if reader.origin.kind != "round" or state is None or not base:
            raise ValueError("actual round state and immutable base required")
        state.verify()
        origin = reader.origin
        if (
            state.identity["attempt_id"] != origin.round.round_id
            or state.identity["sample_id"] != origin.round.sample_id
            or state.identity["window_id"] != origin.round.window_id
            or state.identity["boot_id"] != origin.boot_id
            or state.read("source_settled") is None
        ):
            raise ValueError("actual round inventory before matching source settlement")
    # Real caller constructs this concrete reader with verified live resources;
    # pure tests may substitute its IO boundary without opening any deployment.
    result = await reader.read(base=base)
    if phase == "post_round" and result.reads_complete and not result.errors:
        result.round_export(reader.origin, list(base))
    return result


def dispatch(action, request):
    expected = {"identity"} | (
        {"marker_id", "sequence"}
        if action == Action.STAMP
        else {"cohort_digest", "native_digest"}
        if action == Action.READY
        else {"native_digest"}
        if action == Action.DONE
        else {"cursor"}
        if action == Action.PROGRESS
        else {"kind", "index"}
        if action == Action.RESULT and ("kind" in request or "index" in request)
        else set()
    )
    if set(request) != expected:
        raise ValueError("unexpected fixed helper request fields")
    state, binding = open_state(request)
    try:
        if action == Action.START:
            asyncio.run(cold_window(state, binding))
            return {"identity": state.identity, "operation": action.value}
        if action == Action.STATUS and state.journal.get("bridge_start", state.key) is None:
            return {"identity": state.identity, "initialized": False}
        state.verify()
        if action == Action.STAMP:
            return {
                "identity": state.identity,
                **state.stamp(request["marker_id"], request["sequence"]),
            }
        if action == Action.READY:
            state.client_ready(request["cohort_digest"], request["native_digest"])
            return {"identity": state.identity, **state.read("client_ready")}
        if action == Action.DONE:
            state.client_done(request["native_digest"])
            return {"identity": state.identity, **state.read("client_done")}
        if action == Action.ABORT:
            state.publish("abort", {"guest_ns": time.monotonic_ns()})
            return {"identity": state.identity, **state.read("abort")}
        if action == Action.STATUS:
            return {
                "identity": state.identity,
                "process": state.process_observation(),
                **{
                    key: state.read(key)
                    for key in (
                        "minimal_ready",
                        "cohort",
                        "client_ready",
                        "running",
                        "client_done",
                        "measurement_closed",
                        "failure",
                        "shutdown_failure",
                        "source_settled",
                    )
                },
            }
        if action == Action.PROGRESS:
            from scripts.execution_capacity.source_export import progress_page

            return {
                "identity": state.identity,
                "progress_page": progress_page(state, request["cursor"]).model_dump(),
            }
        if action == Action.RESULT:
            if "kind" in request:
                from scripts.execution_capacity.observers import RecoveryJournal
                from scripts.execution_capacity.source_export import source_shard

                with RecoveryJournal(Path("/capacity-live")) as journal:
                    shard = source_shard(state, journal, request["kind"], request["index"])
                return {"identity": state.identity, "source_shard": shard.model_dump()}
            if state.read("source_settled") is None:
                raise ValueError("source result unavailable before settlement")
            return {
                "identity": state.identity,
                "source": state.read("source_result"),
                "settlement": state.read("source_settled"),
            }
        raise ValueError("unsupported guest command")
    finally:
        state.close()


def main():
    args = parser().parse_args()
    if len(args.request.encode()) > 32768:
        raise ValueError("request exceeds fixed bound")
    result = dispatch(Action(args.action), json.loads(args.request))
    raw = json.dumps(result, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > 1024 * 1024:
        raise ValueError("result exceeds transport bound; retain private source journal")
    print(raw)


if __name__ == "__main__":
    main()
