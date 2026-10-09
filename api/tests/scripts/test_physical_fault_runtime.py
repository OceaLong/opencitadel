from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from scripts.acceptance.physical_fault_runtime import ReadProxy, shell_return
from scripts.acceptance.physical_faults import FaultControl, FaultError, selected_context


def value(kind):
    return {
        "kind": kind,
        "fault_id": str(uuid4()),
        "boot_id": str(uuid4()),
        "execution_run_id": str(uuid4()),
        "activity_id": str(uuid4()),
        "generation": 0,
        "owner_user_id": "owner",
        "source_sha256": "a" * 64,
        "expires_ns": 10000,
        "sandbox_id": "owned",
        "marker_path": "/home/ubuntu/owned",
        "arguments": {"session_id": "shell", "exec_dir": "/home/ubuntu", "command": "write"},
        "storage_key": "private",
    }


@pytest.mark.asyncio
async def test_receipt_is_withheld_only_after_physical_success_and_read_witness(tmp_path):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    arm = value("shell_receipt_loss")
    control.arm(arm)
    events = []
    result = NS(
        success=True,
        data={"status": "completed", "returncode": 0},
        model_dump=lambda **kw: {"success": True},
    )

    async def original(self, session_id, exec_dir, command):
        await self.client.post(
            self._base_url + "/api/shell/exec-command",
            json={"session_id": session_id, "exec_dir": exec_dir, "command": command},
        )
        return result

    class Sandbox:
        id = "owned"
        _base_url = "http://owned"

        def __init__(self):
            async def post(*args, **kwargs):
                events.append("physical")

            self.client = NS(post=post)

        async def read_file(self, *args, **kw):
            events.append("read")
            return NS(success=True, data={"content": "owned-write\n"})

    with selected_context({"arm": arm, "control": control}), pytest.raises(httpx.ReadError):
        await shell_return(original, Sandbox(), **arm["arguments"])
    assert events == ["physical", "read"]
    assert [r["event"] for r in control.rows(arm)] == ["arm", "physical_send", "receipt", "trigger"]
    with (
        selected_context({"arm": arm, "control": control}),
        pytest.raises(FaultError, match="duplicate"),
    ):
        await shell_return(original, Sandbox(), **arm["arguments"])
    assert events == ["physical", "read"]


@pytest.mark.asyncio
async def test_nonselected_read_delegates_and_exact_read_fires_once(tmp_path):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    arm = value("recorded_object_missing")
    control.arm(arm)

    class Objects:
        async def get_bytes(self, key):
            return key.encode()

    proxy = ReadProxy(Objects())
    assert await proxy.get_bytes("private") == b"private"
    with selected_context({"arm": arm, "control": control, "adapter_read": True}):
        assert await proxy.get_bytes("other") == b"other"
        with pytest.raises(FileNotFoundError):
            await proxy.get_bytes("private")
        with pytest.raises(FaultError):
            await proxy.get_bytes("private")


@pytest.mark.asyncio
async def test_failed_or_running_physical_response_is_not_write_proof(tmp_path):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    arm = value("shell_receipt_loss")
    control.arm(arm)

    async def original(self, *args, **kwargs):
        return NS(success=True, data={"status": "running", "returncode": None})

    with selected_context({"arm": arm, "control": control}), pytest.raises(FaultError):
        await shell_return(original, NS(id="owned", client=NS(post=None)), **arm["arguments"])
    assert "trigger" not in [r["event"] for r in control.rows(arm)]


@pytest.mark.asyncio
async def test_cancellation_restores_owned_client_and_task_context(tmp_path):
    import asyncio

    control = FaultControl(tmp_path / "control", now=lambda: 100)
    arm = value("shell_receipt_loss")
    control.arm(arm)

    async def post(*args, **kwargs):
        raise asyncio.CancelledError()

    sandbox = NS(id="owned", _base_url="http://owned", client=NS(post=post))

    async def original(self, session_id, exec_dir, command):
        await self.client.post(
            self._base_url + "/api/shell/exec-command",
            json={"session_id": session_id, "exec_dir": exec_dir, "command": command},
        )

    with selected_context({"arm": arm, "control": control}), pytest.raises(asyncio.CancelledError):
        await shell_return(original, sandbox, **arm["arguments"])
    assert sandbox.client.post is post
    assert [row["event"] for row in control.rows(arm)] == ["arm", "physical_send"]


@pytest.mark.asyncio
async def test_original_replay_adapter_translates_exact_read_fault_without_consumption(tmp_path):
    import hashlib
    from contextlib import asynccontextmanager

    from app.application.evaluation.replay_adapter import ReplayAdapter
    from app.domain.evaluation.errors import ReplayMismatch
    from app.domain.evaluation.recording import (
        MatchRule,
        RecordedContract,
        RecordingSlot,
        recording_key,
    )
    from app.domain.models.tool_policy import CONSERVATIVE_TOOL_POLICY

    contract = RecordedContract(
        name="write",
        pack="test",
        schema_body={"function": {"parameters": {"type": "object", "properties": {}}}},
        policy=CONSERVATIVE_TOOL_POLICY,
        binding_revision="1",
        authority_revision="1",
    )
    body = b"{}"
    slot = RecordingSlot(
        id=uuid4(),
        tool="write",
        contract_digest=contract.digest,
        match_key=recording_key("write", contract.digest, {}, "root", 0),
        rule=MatchRule(),
        branch="root",
        ordinal=0,
        object_id=uuid4(),
        result_digest=hashlib.sha256(body).hexdigest(),
        result_bytes=2,
        simulated_effect=True,
    )
    events = []

    class Repo:
        async def lock_call(self, *args):
            pass

        async def consumed(self, *args):
            return None

        async def object(self, *args):
            return {"storage_key": "private", "digest": slot.result_digest, "size_bytes": 2}

        async def consume(self, *args):
            events.append("consumed")

    class Authority:
        @asynccontextmanager
        async def open(self, context):
            yield NS(
                manifest=NS(id=uuid4(), revision=1, slots=[slot], contracts=[contract]),
                repo=Repo(),
                scope="scope",
                uow=NS(commit=self.commit),
            )

        async def approve(self, *args):
            events.append("approved")

        async def commit(self):
            events.append("committed")

    class Objects:
        async def get_bytes(self, key):
            events.append("real_read")
            return body

    objects = Objects()
    assert await objects.get_bytes("private") == body
    adapter = ReplayAdapter(Authority(), ReadProxy(objects))
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    arm = value("recorded_object_missing")
    control.arm(arm)
    with (
        selected_context({"arm": arm, "control": control, "adapter_read": True}),
        pytest.raises(ReplayMismatch) as failure,
    ):
        await adapter.match(
            NS(run=NS(run_id=uuid4()), activity_id=uuid4()), "write", contract.digest, {}, "root", 0
        )
    assert failure.value.reason == "recorded_object_missing"
    assert events == ["real_read", "approved"]


@pytest.mark.asyncio
async def test_marker_cleanup_requires_actual_absence_after_delete():
    from scripts.acceptance.physical_fault_runtime import cleanup_marker

    class Sandbox:
        async def delete_file(self, path):
            return NS(success=True, data={"deleted": True})

        async def check_file_exists(self, path):
            return NS(success=True, data={"exists": True})

    with pytest.raises(FaultError):
        await cleanup_marker(Sandbox(), "/owned")
