"""Cumulative ownership/settlement and lifecycle with acquisition boundaries only."""

import asyncio
from types import SimpleNamespace

import pytest
from scripts.execution_capacity import cumulative_cleanup as source


def inventory():
    return SimpleNamespace(
        owners=[
            {"stream_id": "run-a", "owner_scope_key": "user:owned"},
            {"stream_id": "run-b", "owner_scope_key": "user:owned"},
        ],
        objects=[],
        attempts=[{"batch_id": "batch-a"}, {"batch_id": "batch-b"}],
        judges=[],
        require_complete=lambda: None,
    )


def rows():
    result = {name: [] for name in source.TABLES}
    result["execution_model_dispatches"] = [
        {"scope_key": "user:owned", "run_id": "run-a", "call_identity": "send-old"},
        {"scope_key": "user:owned", "run_id": "run-a", "call_identity": "send-new"},
    ]
    result["evaluation_budget_reservations"] = [
        {
            "scope_key": "user:owned",
            "call_identity": "send-old",
            "state": "unknown",
            "settlement": None,
        },
        {
            "scope_key": "user:owned",
            "call_identity": "send-new",
            "state": "settled",
            "settlement": {"tokens": 2},
        },
    ]
    result["execution_model_settlements"] = [
        {"scope_key": "user:owned", "call_identity": "send-new", "fact": {"tokens": 2}}
    ]
    result["evaluation_batches"] = [
        {
            "id": "batch-a",
            "scope_key": "user:owned",
            "status": "completed",
            "cleanup_status": "clean",
        },
        {
            "id": "batch-b",
            "scope_key": "user:owned",
            "status": "cancelled",
            "cleanup_status": "clean",
        },
    ]
    return result


def test_old_unknown_dispatch_survives_new_success_and_cumulative_batches():
    result = source.classify(rows(), inventory())
    assert any(r["identity"] == "send-old" and r["state"] == "pending" for r in result)
    assert any(r["identity"] == "send-new" and r["state"] == "settled" for r in result)
    assert {r["identity"] for r in result if r["kind"] == "evaluation_batches"} == {
        "batch-a",
        "batch-b",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_run",
        "orphan_reservation",
        "bucket",
        "late_claim",
        "missing_batch",
        "unknown_object",
    ],
)
def test_no_all_clean_from_missing_or_foreign_cumulative_facts(mutation):
    data = rows()
    data["execution_model_dispatches"] = []
    data["evaluation_budget_reservations"] = []
    data["execution_model_settlements"] = []
    if mutation == "foreign_run":
        data["execution_activity_tasks"] = [{"aggregate_id": "foreign", "status": "succeeded"}]
    elif mutation == "orphan_reservation":
        data["evaluation_budget_reservations"] = [
            {
                "scope_key": "user:owned",
                "call_identity": "orphan",
                "state": "settled",
                "settlement": {},
            }
        ]
    elif mutation == "bucket":
        data["evaluation_budget_buckets"] = [
            {"key": "scope", "slots": 1, "reserved_tokens": 0, "reserved_money": 0}
        ]
    elif mutation == "late_claim":
        data["execution_activity_tasks"] = [
            {"aggregate_id": "run-a", "status": "succeeded", "claim_deadline": "future"}
        ]
    elif mutation == "missing_batch":
        data["evaluation_batches"].pop()
    else:
        data["evaluation_object_intents"] = [
            {"id": "object", "storage_key": "foreign", "cleaned_at": None}
        ]
    assert any(r["state"] in {"pending", "error"} for r in source.classify(data, inventory()))


def test_diagnostics_failure_does_not_skip_writer_shutdown_or_final_after_exit_inventory():
    calls = []

    class Writers:
        async def stop_admissions(self):
            calls.append("admissions")

        async def stop(self):
            calls.append("stop")
            return [{"exited": True}]

    class Workload:
        async def finish(self):
            calls.append("settlement")

    class Diagnostics:
        async def collect(self):
            calls.append("diagnostics")
            raise ValueError("invalid original trace")

    class Uploads:
        async def drain(self):
            calls.append("uploads")

    class Final:
        async def read(self):
            calls.append("final")
            return {"issues": [], "complete": True}

    result = asyncio.run(
        source.quiesce(
            writers=Writers(),
            workloads=[Workload()],
            diagnostics=[Diagnostics()],
            uploads=[Uploads()],
            final=Final(),
        )
    )
    assert calls == ["admissions", "settlement", "diagnostics", "uploads", "stop", "final"]
    assert not result.complete
    assert result.errors[0]["stage"] == "diagnostics"


def test_unobserved_writer_exit_prevents_final_quiescence_read():
    calls = []

    class Writers:
        async def stop_admissions(self):
            pass

        async def stop(self):
            return [{"exited": False}]

    class Final:
        async def read(self):
            calls.append("final")

    result = asyncio.run(
        source.quiesce(writers=Writers(), workloads=[], diagnostics=[], uploads=[], final=Final())
    )
    assert not calls
    assert not result.complete
    assert result.errors


def test_actual_container_exit_required_even_when_stop_returns(tmp_path):
    import copy
    import json

    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.writer_lifecycle import ContainerWriters

    tmp_path.chmod(0o700)
    container = {
        "Id": "kernel",
        "Image": "image",
        "Config": {"Hostname": "writer-host"},
        "Mounts": [
            {
                "Destination": "/capacity-writers",
                "Type": "bind",
                "Source": str(tmp_path),
                "RW": True,
            }
        ],
        "HostConfig": {},
        "State": {
            "Running": True,
            "Status": "running",
            "StartedAt": "start",
            "Pid": 100,
            "ExitCode": 0,
            "FinishedAt": "",
        },
    }
    provider = copy.deepcopy(container)
    provider["Id"] = "provider"
    actual = {"kernel": container, "provider": provider}
    with RecoveryJournal(tmp_path) as journal:
        journal.intent(
            "writer",
            "writer-id",
            {
                "invocation": "attempt",
                "source_sha256": "source",
                "hostname": "writer-host",
                "pid": 1,
                "boot_id": "boot",
                "start_ticks": 7,
                "pid_namespace": 8,
            },
        )
    deployment = SimpleNamespace(
        binding={
            "containers": {
                "kernel": {"service": "opencitadel-execution-kernel"},
                "provider": {"service": "inference-provider"},
            },
            "provider_container": "provider",
            "writer_journal_root": str(tmp_path),
            "invocation": "attempt",
            "source_sha256": "source",
        },
        children={},
        verify=lambda: ({"kernel": True}, actual),
    )

    def docker(*args):
        if args[0] == "exec":
            return json.dumps(
                {"pid": 1, "boot_id": "boot", "start_ticks": 7, "pid_namespace": 8}
            ).encode()
        if args[0] == "stop":
            return b""  # supervisor/command success is not actual exit
        return json.dumps([actual[args[-1]]]).encode()

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget
    from scripts.execution_capacity.test_writer_replay import bounded_inspect_fixture

    budget = EvidenceBudget()
    writers = ContainerWriters(
        deployment,
        docker,
        journal_root=tmp_path,
        budget=budget,
        inspect_transport=bounded_inspect_fixture(budget, docker),
    )
    observed = asyncio.run(writers.stop())
    assert all(not row["exited"] for row in observed)
    with pytest.raises(ValueError, match="actually exit"):
        writers.final_journals()
    for row in actual.values():
        row["State"].update(Running=False, Status="exited", Pid=0, FinishedAt="finished")
    observed = asyncio.run(writers.stop())
    assert all(row["exited"] for row in observed)
    assert writers.final_journals()["issues"]  # absent resource/supervisor receipts


def test_actual_process_start_namespace_and_boot_cannot_be_replaced(tmp_path):
    import json

    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.writer_lifecycle import ContainerWriters

    tmp_path.chmod(0o700)
    row = {
        "Id": "kernel",
        "Image": "image",
        "Config": {"Hostname": "same-hostname"},
        "Mounts": [
            {
                "Destination": "/capacity-writers",
                "Type": "bind",
                "Source": str(tmp_path),
                "RW": True,
            }
        ],
        "HostConfig": {},
        "State": {
            "Running": True,
            "Status": "running",
            "StartedAt": "new",
            "Pid": 100,
            "ExitCode": 0,
            "FinishedAt": "",
        },
    }
    provider = {**row, "Id": "provider"}
    with RecoveryJournal(tmp_path) as journal:
        journal.intent(
            "writer",
            "current",
            {
                "invocation": "attempt",
                "source_sha256": "source",
                "hostname": "same-hostname",
                "pid": 1,
                "boot_id": "old-boot",
                "start_ticks": 7,
                "pid_namespace": 8,
            },
        )
    binding = {
        "containers": {
            "kernel": {"service": "opencitadel-execution-kernel"},
            "provider": {"service": "inference-provider"},
        },
        "provider_container": "provider",
        "writer_journal_root": str(tmp_path),
        "invocation": "attempt",
        "source_sha256": "source",
    }
    deployment = SimpleNamespace(
        binding=binding,
        children={},
        verify=lambda: ({"kernel": True}, {"kernel": row, "provider": provider}),
    )

    def docker(*args):
        return json.dumps(
            {"pid": 1, "boot_id": "new-boot", "start_ticks": 7, "pid_namespace": 8}
        ).encode()

    with pytest.raises(ValueError, match="process incarnation"):
        ContainerWriters(deployment, docker, journal_root=tmp_path)


def test_actual_decimal_zero_budget_remains_settled():
    data = rows()
    data["evaluation_budget_buckets"] = [
        {"key": "scope", "slots": 0, "reserved_tokens": 0, "reserved_money": "0.000000"}
    ]
    assert (
        next(
            r
            for r in source.classify(data, inventory())
            if r["kind"] == "evaluation_budget_buckets"
        )["state"]
        == "retained"
    )


def test_disabled_job_still_requires_exact_owner():
    data = rows()
    data["scheduled_jobs"] = [
        {"id": "foreign", "enabled": False, "owner_user_id": "foreign", "team_id": None}
    ]
    assert (
        next(r for r in source.classify(data, inventory()) if r["kind"] == "scheduled_jobs")[
            "state"
        ]
        == "error"
    )


@pytest.mark.parametrize("historical_run", ["run-a", "run-b"])
@pytest.mark.parametrize(
    "state",
    [
        {"failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN"},
        {"settled_activities": [["activity", "unknown"]]},
        {"activity_failure_codes": [["activity", 1, "NON_IDEMPOTENT_OUTCOME_UNKNOWN"]]},
    ],
)
def test_released_historical_subject_and_judge_leases_retain_each_unknown_effect(
    historical_run, state
):
    data = rows()
    facts = inventory()
    facts.attempts = [{"batch_id": "batch-a", "run_id": "run-a"}]
    facts.judges = [{"batch_id": "batch-b", "run_id": "run-b"}]
    data["evaluation_execution_leases"] = [
        {
            "id": "old-lease",
            "scope_key": "user:owned",
            "run_id": historical_run,
            "phase": "released",
            "state": state,
        },
        {
            "id": "new-lease",
            "scope_key": "user:owned",
            "run_id": historical_run,
            "phase": "released",
            "state": {"settled_activities": [["activity", "succeeded"]]},
        },
    ]
    result = {
        r["identity"]: r
        for r in source.classify(data, facts)
        if r["kind"] == "evaluation_execution_leases"
    }
    assert result["old-lease"]["state"] == "pending"
    assert result["new-lease"]["state"] == "retained"
    assert data["evaluation_execution_leases"][0]["state"] == state
