"""Transparent wrappers installed only by the owned acceptance kernel launcher."""

import functools
import hashlib

import httpx

try:
    from .physical_fault_authority import verify_started
    from .physical_faults import FaultError, actual_context, digest, selected_context
except ImportError:
    from physical_fault_authority import verify_started
    from physical_faults import FaultError, actual_context, digest, selected_context


class ReadProxy:
    def __init__(self, objects):
        self.objects = objects

    async def get_bytes(self, key):
        selected = actual_context()
        if selected and selected.get("adapter_read") and key == selected["arm"]["storage_key"]:
            arm, control = selected["arm"], selected["control"]
            control.selected(arm["execution_run_id"], arm["activity_id"], arm["boot_id"])
            control.event(
                arm,
                "trigger",
                {
                    "object_id": arm.get("object_id"),
                    "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                },
            )
            raise FileNotFoundError("acceptance recorded object read unavailable")
        return await self.objects.get_bytes(key)


async def shell_return(original, self, session_id, exec_dir, command):
    selected = actual_context()
    if not selected or selected["arm"]["kind"] != "shell_receipt_loss":
        return await original(self, session_id, exec_dir, command)
    arm, control = selected["arm"], selected["control"]
    arguments = {"session_id": session_id, "exec_dir": exec_dir, "command": command}
    if self.id != arm["sandbox_id"] or arguments != arm["arguments"]:
        raise FaultError("physical sandbox/arguments changed")
    control.selected(arm["execution_run_id"], arm["activity_id"], arm["boot_id"])
    # Count the actual HTTP dispatch boundary on this owned client, not the catalog.
    original_post = self.client.post

    async def post(url, *args, **kwargs):
        active = actual_context()
        if active is selected and str(url) == f"{self._base_url}/api/shell/exec-command":
            if kwargs.get("json") != arguments:
                raise FaultError("physical request changed")
            control.event(arm, "physical_send", {"sandbox_id": self.id})
        return await original_post(url, *args, **kwargs)

    self.client.post = post
    try:
        result = await original(self, session_id, exec_dir, command)
    finally:
        self.client.post = original_post
    if (
        not result.success
        or not isinstance(result.data, dict)
        or result.data.get("status") != "completed"
        or result.data.get("returncode") != 0
    ):
        raise FaultError("physical write has no completed exit0 receipt")
    witness = await self.read_file(arm["marker_path"], max_length=128)
    if (
        not witness.success
        or not isinstance(witness.data, dict)
        or witness.data.get("content") != "owned-write\n"
    ):
        raise FaultError("physical marker witness differs from exactly one write")
    rows = control.rows(arm)
    if sum(row["event"] == "physical_send" for row in rows) != 1:
        raise FaultError("actual physical send absent")
    control.selected(arm["execution_run_id"], arm["activity_id"], arm["boot_id"])
    # Retain actual receipt/witness privately, before discarding the application return.
    with control.lock():
        control.write(
            "receipt-" + arm["fault_id"] + ".json",
            {"receipt": result.model_dump(mode="json"), "witness": witness.data},
        )
    control.event(
        arm,
        "receipt",
        {
            "sandbox_id": self.id,
            "receipt_sha256": digest(result.model_dump(mode="json")),
            "witness_sha256": hashlib.sha256(b"owned-write\n").hexdigest(),
            "marker_lines": 1,
            "returncode": 0,
        },
    )
    control.event(
        arm,
        "trigger",
        {"exception": "httpx.ReadError", "boundary": "sandbox return-boundary receipt loss"},
    )
    raise httpx.ReadError("acceptance owned shell receipt withheld")


def install(control, log):
    from app.application.evaluation.replay_adapter import ReplayAdapter
    from app.application.evaluation.replay_runtime import ReplayRuntime
    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from app.domain.evaluation.errors import ReplayMismatch
    from app.infrastructure.external.sandbox.docker_sandbox import DockerSandbox

    handler = ToolCallActivityHandler.execute

    @functools.wraps(handler)
    async def execute(self, request, context):
        arm = control.selected(context.run.run_id, context.activity_id, log.boot_id)
        if arm is None:
            return await handler(self, request, context)
        if arm["source_sha256"] != log.binding["source_sha256"]:
            raise FaultError("fault source changed")
        facts = await verify_started(self._replay.authority.uow_factory, arm, request, context)
        control.event(arm, "enter", facts)
        outcome = "returned"
        try:
            with selected_context({"arm": arm, "control": control}):
                return await handler(self, request, context)
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            control.event(arm, "exit", {"outcome": outcome})

    ToolCallActivityHandler.execute = execute

    initialize = ReplayRuntime.__init__

    def init(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        self.adapter.objects = ReadProxy(self.adapter.objects)

    ReplayRuntime.__init__ = init

    match = ReplayAdapter.match

    @functools.wraps(match)
    async def matching(self, context, tool, contract, args, branch, ordinal, *, parallel_group=""):
        selected = actual_context()
        if not selected or selected["arm"]["kind"] != "recorded_object_missing":
            return await match(
                self, context, tool, contract, args, branch, ordinal, parallel_group=parallel_group
            )
        arm = selected["arm"]
        # Current original authority is checked again, with real worker context.
        async with self.authority.open(context) as access:
            slots = [s for s in access.manifest.slots if str(s.id) == arm["slot_id"]]
            if (
                str(access.manifest.id) != arm["version_id"]
                or access.manifest.revision != arm["revision"]
                or len(slots) != 1
            ):
                raise FaultError("recording authority changed at adapter")
            slot = slots[0]
            obj = await access.repo.object(access.scope, slot.object_id)
            if (str(slot.object_id), obj["storage_key"], obj["digest"], obj["size_bytes"]) != (
                arm["object_id"],
                arm["storage_key"],
                arm["object_digest"],
                arm["object_bytes"],
            ) or (tool, contract, branch, parallel_group, ordinal) != (
                slot.tool,
                slot.contract_digest,
                slot.branch,
                slot.parallel_group,
                slot.ordinal,
            ):
                raise FaultError("actual adapter slot changed")
        try:
            with selected_context({**selected, "adapter_read": True}):
                return await match(
                    self,
                    context,
                    tool,
                    contract,
                    args,
                    branch,
                    ordinal,
                    parallel_group=parallel_group,
                )
        except ReplayMismatch as error:
            if error.reason == "recorded_object_missing":
                control.event(arm, "mismatch", {"reason": error.reason})
            raise

    ReplayAdapter.match = matching
    original_shell = DockerSandbox.exec_command

    @functools.wraps(original_shell)
    async def shell(self, session_id, exec_dir, command):
        return await shell_return(original_shell, self, session_id, exec_dir, command)

    DockerSandbox.exec_command = shell


async def cleanup_marker(sandbox, path):
    removed = await sandbox.delete_file(path)
    if (
        not removed.success
        or not isinstance(removed.data, dict)
        or removed.data.get("deleted") is not True
    ):
        raise FaultError("owned marker deletion unconfirmed")
    absent = await sandbox.check_file_exists(path)
    if (
        not absent.success
        or not isinstance(absent.data, dict)
        or absent.data.get("exists") is not False
    ):
        raise FaultError("owned marker absence unconfirmed")
