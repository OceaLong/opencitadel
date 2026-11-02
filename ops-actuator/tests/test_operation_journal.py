"""Failure boundaries for durable receipts, target gates and fixed-patch recovery."""

import copy

import pytest
from kubernetes.client.exceptions import ApiException
from opencitadel_ops_actuator.k8s_writer import OPERATION_ANNOTATION, PAYLOAD_ANNOTATION
from opencitadel_ops_actuator.operation_journal import (
    MAX_JOURNAL_BYTES,
    RECEIPT_RESERVE_BYTES,
    ConfigMapJournal,
    JournalConflict,
    JournalFailure,
)


@pytest.mark.asyncio
async def test_lost_patch_response_resolves_same_operation(make_actuator, invoke, fake_k8s):
    fake_k8s.lose_response = True
    result = await invoke(make_actuator(), "restart_workload")
    assert result["state"] == "applied"
    assert (await make_actuator().get_operation(result["operation_id"]))["receipt"] == result[
        "receipt"
    ]
    assert fake_k8s.patch_attempts == 1


@pytest.mark.asyncio
async def test_receipt_failure_blocks_followup_until_receipt_is_durable(
    make_actuator, invoke, fake_k8s
):
    fake_k8s.core.fail_receipt = True
    assert (await invoke(make_actuator(), "restart_workload", key="a"))["state"] == "unknown"
    assert (await invoke(make_actuator(), "restart_workload", key="b"))["state"] == "pending"
    assert fake_k8s.patch_attempts == 1
    fake_k8s.core.fail_receipt = False
    assert (await make_actuator().get_operation("operation:a"))["state"] == "applied"
    assert fake_k8s.patch_attempts == 1
    assert (await invoke(make_actuator(), "restart_workload", key="b"))["state"] == "applied"
    assert fake_k8s.patch_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["overwritten", "recreated", "missing", "denied"])
async def test_missing_recovery_proof_never_means_not_applied(
    make_actuator, invoke, fake_k8s, loss, monkeypatch
):
    fake_k8s.core.fail_receipt = True
    await invoke(make_actuator(), "scale_workload", replicas=5)
    fake_k8s.core.fail_receipt = False
    if loss == "overwritten":
        fake_k8s.annotations.clear()
    elif loss == "recreated":
        fake_k8s.uid = "uid-recreated"
    elif loss == "missing":
        fake_k8s.missing = True
    else:

        def denied(*args):
            raise ApiException(status=403)

        monkeypatch.setattr(fake_k8s, "observe", denied)
    result = await make_actuator().get_operation("operation:key-1")
    assert result["state"] == "unknown"
    assert result["receipt"] is None
    assert fake_k8s.patch_attempts == 1


@pytest.mark.asyncio
async def test_current_replicas_alone_are_not_proof(make_actuator, invoke, fake_k8s):
    fake_k8s.raise_on_mutation = TimeoutError()
    await invoke(make_actuator(), "scale_workload", replicas=5)
    fake_k8s.replicas = 5
    assert (await make_actuator().get_operation("operation:key-1"))["state"] == "unknown"
    assert fake_k8s.scale_calls == 0


@pytest.mark.asyncio
async def test_recovery_reuses_patch_and_original_resource_version(make_actuator, invoke, fake_k8s):
    fake_k8s.raise_on_mutation = TimeoutError()
    first = await invoke(make_actuator(), "restart_workload")
    assert first["state"] == "unknown"
    journal = ConfigMapJournal(make_actuator().settings, core=fake_k8s.core)
    original = copy.deepcopy(journal.find_operation(first["operation_id"]).value["patch"])
    fake_k8s.raise_on_mutation = None
    assert (await invoke(make_actuator(), "restart_workload"))["state"] == "applied"
    assert fake_k8s.last_patch == original
    assert fake_k8s.restart_calls == 1


@pytest.mark.asyncio
async def test_resource_version_conflict_never_rebases_patch(make_actuator, invoke, fake_k8s):
    fake_k8s.raise_on_mutation = TimeoutError()
    await invoke(make_actuator(), "restart_workload")
    fake_k8s.raise_on_mutation = None
    fake_k8s.resource_version += 1
    assert (await invoke(make_actuator(), "restart_workload"))["state"] == "unknown"
    assert fake_k8s.restart_calls == 0
    assert fake_k8s.patch_attempts == 2


@pytest.mark.asyncio
async def test_journal_denied_is_fail_closed_even_if_target_has_marker(
    make_actuator, invoke, fake_k8s
):
    fake_k8s.core.fail = True
    fake_k8s.annotations[OPERATION_ANNOTATION] = "operation:key-1"
    result = await invoke(make_actuator(), "restart_workload")
    assert result["error_code"] == "OPERATION_JOURNAL_UNAVAILABLE"
    assert fake_k8s.patch_attempts == 0


@pytest.mark.asyncio
async def test_uid_precondition_is_retained_rejection(make_actuator, invoke, fake_k8s):
    result = await invoke(make_actuator(), "restart_workload", target_uid="old-uid")
    assert result["state"] == "not_applied"
    assert result["error_code"] == "TARGET_UID_MISMATCH"
    assert fake_k8s.patch_attempts == 0
    assert (await make_actuator().get_operation(result["operation_id"]))["receipt"] == result[
        "receipt"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "literal",
    [
        {"spec": {"containers": [{"env": [{"name": "VALUE", "value": "private"}]}]}},
        {"spec": {"containers": [{"args": ["--password=private"]}]}},
        {"metadata": {"annotations": {"note": "password=private"}}},
    ],
)
async def test_sensitive_rollback_never_persists_literal_or_patches(
    make_actuator, invoke, fake_k8s, literal
):
    fake_k8s.revisions = [literal, {}]
    result = await invoke(make_actuator(), "rollback_workload")
    assert result["error_code"] == "OPERATION_JOURNAL_SENSITIVE"
    assert result["state"] == "not_applied"
    assert fake_k8s.patch_attempts == 0
    assert all(
        "private" not in item["data"]["record.json"] for item in fake_k8s.core.items.values()
    )


@pytest.mark.asyncio
async def test_oversized_rollback_rejects_before_send(make_actuator, invoke, fake_k8s):
    fake_k8s.revisions = [{"metadata": {"annotations": {"bulk": "x" * MAX_JOURNAL_BYTES}}}, {}]
    result = await invoke(make_actuator(), "rollback_workload")
    assert result["error_code"] == "OPERATION_JOURNAL_TOO_LARGE"
    assert fake_k8s.patch_attempts == 0
    assert result["state"] == "not_applied"


@pytest.mark.asyncio
async def test_read_is_bound_to_current_authority_and_registration(
    make_actuator, actuator_settings, invoke
):
    await invoke(make_actuator(), "restart_workload")
    other = make_actuator(actuator_settings(caller_authority="another-caller"))
    assert (await other.get_operation("operation:key-1"))["error_code"] == "OPERATION_NOT_FOUND"
    revoked = make_actuator(actuator_settings(allowed_workloads={}))
    assert (await revoked.get_operation("operation:key-1"))["error_code"] == "TARGET_DENIED"


@pytest.mark.asyncio
async def test_terminal_receipt_survives_target_deletion_without_client(
    make_actuator, invoke, fake_k8s
):
    result = await invoke(make_actuator(), "restart_workload")
    fake_k8s.missing = True
    actuator = make_actuator()

    def unavailable():
        raise AssertionError("terminal receipt does not need a target client")

    actuator._k8s_factory = unavailable
    assert (await actuator.get_operation(result["operation_id"]))["receipt"] == result["receipt"]


def test_config_map_cas_and_reserved_space(actuator_settings, fake_k8s):
    journal = ConfigMapJournal(actuator_settings(), core=fake_k8s.core)
    first = journal.create("test-record", {"state": "prepared", "data": "small"})
    with pytest.raises(JournalConflict):
        journal.create("test-record", {"data": "different"})
    updated = journal.replace(first, {"state": "sending"})
    with pytest.raises(JournalConflict):
        journal.replace(first, {"state": "unknown"})
    assert journal.read("test-record") == updated
    with pytest.raises(JournalFailure, match="512 KiB"):
        journal.create(
            "oversized",
            {"state": "prepared", "data": "x" * (MAX_JOURNAL_BYTES - RECEIPT_RESERVE_BYTES)},
        )


@pytest.mark.asyncio
async def test_receipt_key_is_permanent_tombstone(make_actuator, invoke, fake_k8s):
    first = await invoke(make_actuator(), "restart_workload")
    journal = ConfigMapJournal(make_actuator().settings, core=fake_k8s.core)
    entry = journal.find_operation(first["operation_id"])
    journal.replace(entry, {**entry.value, "created_at": "2000-01-01T00:00:00+00:00"})
    assert (await invoke(make_actuator(), "restart_workload"))["receipt"] == first["receipt"]
    assert fake_k8s.restart_calls == 1


@pytest.mark.asyncio
async def test_query_wrong_payload_marker_is_unknown(make_actuator, invoke, fake_k8s):
    fake_k8s.core.fail_receipt = True
    await invoke(make_actuator(), "restart_workload")
    fake_k8s.core.fail_receipt = False
    fake_k8s.annotations[PAYLOAD_ANNOTATION] = "f" * 64
    assert (await make_actuator().get_operation("operation:key-1"))["state"] == "unknown"


@pytest.mark.asyncio
async def test_denied_retry_cannot_claim_historical_operation_not_applied(
    make_actuator, actuator_settings, invoke
):
    first = await invoke(make_actuator(), "restart_workload")
    revoked = make_actuator(actuator_settings(allowed_workloads={}))
    rejected = await invoke(revoked, "restart_workload")
    assert rejected["action_outcome"] == "failed"
    assert rejected["state"] == "unknown"
    assert rejected["receipt"] is None
    assert (await make_actuator().get_operation(first["operation_id"]))["state"] == "applied"


@pytest.mark.asyncio
async def test_terminal_execute_replay_does_not_need_target_client(make_actuator, invoke):
    first = await invoke(make_actuator(), "restart_workload")
    actuator = make_actuator()

    def unavailable():
        raise AssertionError("cached terminal receipt does not need target client")

    actuator._k8s_factory = unavailable
    replay = await invoke(actuator, "restart_workload")
    assert replay["receipt"] == first["receipt"]


@pytest.mark.asyncio
async def test_small_response_limit_never_truncates_receipt(
    make_actuator, actuator_settings, invoke
):
    result = await invoke(
        make_actuator(actuator_settings(max_output_bytes=1024)), "restart_workload"
    )
    assert result["error_code"] == "OUTPUT_TOO_LARGE"
    assert result["state"] == "unknown"
    assert result["receipt"] is None
    assert (await make_actuator().get_operation("operation:key-1"))["state"] == "applied"
