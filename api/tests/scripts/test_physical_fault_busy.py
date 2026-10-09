"""No resources opened: exercise command contention's actual revalidation helper."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/acceptance"))
import physical_fault_command as command
from physical_faults import FaultControl, FaultError

from tests.scripts.test_physical_fault_control import bound_arm


def test_failure_code_only_exposes_reviewed_static_fault_reasons():
    assert (
        command.failure_code(FaultError("recorded slot ambiguous"))
        == "FaultError:recorded_slot_ambiguous"
    )
    assert command.failure_code(FaultError("private object://secret/payload")) == "FaultError"
    assert command.failure_code(ValueError("recorded slot ambiguous")) == "ValueError"


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["ordinary", "trigger", "removed", "approval_lost"])
async def test_busy_rechecks_pending_and_locked_state_after_race(tmp_path, monkeypatch, race):
    control = FaultControl(tmp_path / "control", now=lambda: 100)
    value = bound_arm()
    control.arm(value)

    async def recheck(*_):
        if race == "trigger":
            control.event(value, "enter", {})
        if race == "removed":
            control.disarm(value["fault_id"], require_complete=False)
        if race == "approval_lost":
            raise FaultError("not pending")

    pending = AsyncMock(side_effect=recheck)
    monkeypatch.setattr(command, "pending", pending)
    data = {**value, "project": value["runner_project"], "run": value["runner_run"]}
    if race in {"trigger", "approval_lost"}:
        with pytest.raises(FaultError):
            await command.retryable_busy(
                control, SimpleNamespace(uow_factory="factory"), data, value
            )
    else:
        assert await command.retryable_busy(
            control, SimpleNamespace(uow_factory="factory"), data, value
        ) is (race == "ordinary")
    pending.assert_awaited_once_with(
        "factory", value["owner_user_id"], value["execution_run_id"], value["activity_id"]
    )
