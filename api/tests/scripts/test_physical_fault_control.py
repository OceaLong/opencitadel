"""Pure filesystem/control checks, never runtime fault evidence."""

import asyncio
from uuid import uuid4

import pytest
from scripts.acceptance.physical_faults import FaultControl, FaultError


def arm():
    return {
        "kind": "recorded_object_missing",
        "fault_id": str(uuid4()),
        "boot_id": str(uuid4()),
        "execution_run_id": str(uuid4()),
        "activity_id": str(uuid4()),
        "generation": 0,
        "owner_user_id": "operator",
        "source_sha256": "a" * 64,
        "expires_ns": 10_000,
        "positive_read": True,
        "storage_key": "private/object",
    }


def test_single_trigger_is_durable_and_second_trigger_cannot_pass(tmp_path):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = arm()
    control.arm(value)
    control.event(value, "trigger", {"claim_generation": 1})
    assert control.rows(value)[-1]["event"] == "trigger"
    with pytest.raises(FaultError, match="duplicate"):
        control.event(value, "trigger", {"claim_generation": 1})
    assert "private/object" not in (control.root / "journal.json").read_text()


def test_exact_identity_expiry_and_concurrent_arm(tmp_path):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = arm()
    control.arm(value)
    with pytest.raises(FaultError, match="active"):
        control.arm(arm())
    assert control.selected("unrelated", value["activity_id"], value["boot_id"]) is None
    with pytest.raises(FaultError, match="boot"):
        control.selected(value["execution_run_id"], value["activity_id"], str(uuid4()))
    control.now = lambda: 20_000
    with pytest.raises(FaultError, match="expired"):
        control.selected(value["execution_run_id"], value["activity_id"], value["boot_id"])
    control.disarm(value["fault_id"], require_complete=False)
    assert not (control.root / "arm.json").exists()


def test_refuses_symlink_and_inflight_disarm(tmp_path):
    root = tmp_path / "control"
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(FaultError):
        FaultControl(root)
    root.unlink()
    control = FaultControl(root, now=lambda: 100)
    value = arm()
    control.arm(value)
    control.event(value, "enter", {})
    with pytest.raises(FaultError, match="inflight"):
        control.disarm(value["fault_id"], require_complete=False)
    control.event(value, "exit", {"outcome": "CancelledError"})
    control.disarm(value["fault_id"], require_complete=False)


def test_cancellation_keeps_evidence_and_removes_task_context():
    from scripts.acceptance.physical_faults import actual_context, selected_context

    async def scenario():
        with selected_context({"activity_id": "owned"}):
            assert actual_context().get("activity_id") == "owned"
            inherited = await asyncio.create_task(asyncio.sleep(0, result=None))
            assert inherited is None

            async def child():
                with pytest.raises(FaultError, match="task"):
                    actual_context()

            await asyncio.create_task(child())
        assert actual_context() is None

    asyncio.run(scenario())


def test_fault_evidence_requires_receipt_before_trigger_and_closed_interval():
    from scripts.acceptance.physical_faults import validate_fault_rows

    value = arm()
    value["kind"] = "shell_receipt_loss"
    events = ["arm", "enter", "physical_send", "receipt", "trigger", "exit"]
    rows = [
        {"event": name, "fault_id": value["fault_id"], "at": index}
        for index, name in enumerate(events)
    ]
    validate_fault_rows(value, rows)
    rows[3], rows[4] = rows[4], rows[3]
    with pytest.raises(FaultError):
        validate_fault_rows(value, rows)


def bound_arm():
    value = arm()
    value.update(
        runner_project="project",
        runner_run="run",
        invocation_id="invocation",
        binding_sha256="b" * 64,
        kernel_container="container",
    )
    return value


@pytest.mark.parametrize("event", [None, "enter", "trigger", "duplicate", "exit"])
def test_busy_requires_exact_untriggered_noninflight_arm(tmp_path, event):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = bound_arm()
    control.arm(value)
    if event:
        control.event(value, event, {})
        with pytest.raises(FaultError):
            control.ordinary_busy(value)
    else:
        assert control.ordinary_busy(value)
    assert control.read("arm.json") == value


@pytest.mark.parametrize(
    "key",
    [
        "runner_project",
        "runner_run",
        "invocation_id",
        "binding_sha256",
        "kernel_container",
        "source_sha256",
        "boot_id",
        "owner_user_id",
    ],
)
def test_busy_rejects_changed_binding_and_preserves_control(tmp_path, key):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = bound_arm()
    control.arm(value)
    with pytest.raises(FaultError):
        control.ordinary_busy({**value, key: "foreign"})
    assert control.read("arm.json") == value


def test_busy_rejects_foreign_or_poisoned_journal(tmp_path):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = bound_arm()
    control.arm(value)
    for rows in [
        [{"event": "arm", "fault_id": "foreign", "at": 100}],
        [{"event": "arm", "fault_id": value["fault_id"], "at": "invalid"}],
        [],
    ]:
        control.write("journal.json", rows)
        with pytest.raises(FaultError):
            control.ordinary_busy(value)


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("kind", "unknown"),
        ("generation", -1),
        ("fault_id", "not-uuid"),
        ("schema_version", 2),
        ("expires_ns", 99),
    ],
)
def test_busy_rejects_invalid_or_expired_control(tmp_path, key, invalid):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = bound_arm()
    control.arm(value)
    control.write("arm.json", {**value, key: invalid})
    with pytest.raises(FaultError):
        control.ordinary_busy(value)
    assert control.read("arm.json")[key] == invalid
