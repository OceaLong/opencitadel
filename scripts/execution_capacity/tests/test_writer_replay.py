"""Actual before/after writer operands, no Docker or process effects."""

from copy import deepcopy

import pytest
from scripts.execution_capacity.host import producer_fingerprint


def inputs():
    before = {
        "Id": "writer",
        "Image": "image",
        "Config": {},
        "HostConfig": {},
        "Mounts": [],
        "State": {
            "StartedAt": "start",
            "Running": True,
            "Status": "running",
            "Pid": 1,
            "ExitCode": 0,
            "FinishedAt": "",
        },
    }
    after = deepcopy(before)
    after["State"].update(Running=False, Status="exited", Pid=0, FinishedAt="end")
    original = {
        "fingerprint": producer_fingerprint(before),
        "started_at": "start",
        "service": "fixture",
        "image": "image",
        "inspection": deepcopy(before),
    }
    return original, before, after


@pytest.mark.parametrize(
    "mutation", [None, "id", "start", "state", "fingerprint", "before", "absent"]
)
def test_shared_writer_exit_uses_original_inspections(mutation):
    from scripts.execution_capacity.writer_lifecycle import writer_exit_observation

    original, before, after = inputs()
    if mutation == "id":
        after["Id"] = "foreign"
    if mutation == "start":
        after["State"]["StartedAt"] = "changed"
    if mutation == "state":
        after["State"]["Pid"] = 1
    if mutation == "fingerprint":
        after["Config"]["Image"] = "different"
    if mutation == "before":
        before["Id"] = "foreign"
    if mutation == "absent":
        after = None
    if mutation in {"before", "absent"}:
        with pytest.raises((ValueError, TypeError)):
            writer_exit_observation(original, "writer", before, after, 123)
    else:
        result = writer_exit_observation(original, "writer", before, after, 123)
        assert result["exited"] is (mutation is None)
        assert result["before"] == before
        assert result["after"] == after


def bounded_inspect_fixture(budget, render, *, output_limit=1024 * 1024, evidence=None):
    import os

    from scripts.execution_capacity.evidence_transport import EvidenceTransport

    class Process:
        def __init__(self, data):
            self.stdout = self.pipe(data)
            self.stderr = self.pipe(b"")

        @staticmethod
        def pipe(data):
            reader, writer = os.pipe()
            os.write(writer, data)
            os.close(writer)
            return os.fdopen(reader, "rb")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def factory(args, **kwargs):
        return Process(render(*args[1:]))

    return EvidenceTransport(
        budget, process_factory=factory, output_limit=output_limit, evidence=evidence
    )


@pytest.mark.parametrize("fault", [None, "quota", "before"])
def test_stop_bounds_actual_inspect_before_decode_and_preserves_stop_command(fault):
    import json

    from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
    from scripts.execution_capacity.writer_lifecycle import ContainerWriters, replay_writer_exits

    original, before, after = inputs()
    current = deepcopy(before)
    if fault == "before":
        current["Id"] = "foreign"
    calls = []

    def docker(*args):
        calls.append(args)
        if args[0] == "stop":
            current.clear()
            current.update(deepcopy(after))
            return b""
        return json.dumps([current]).encode()

    budget = EvidenceBudget()
    writer = object.__new__(ContainerWriters)
    writer.budget = budget
    writer.original = {"writer": original}
    writer.exits = {}
    writer.exit_observations = []
    writer.docker = docker
    writer.inspect_transport = bounded_inspect_fixture(
        budget, docker, output_limit=32 if fault == "quota" else 1024 * 1024
    )
    if fault:
        with pytest.raises(EvidenceQuotaError if fault == "quota" else ValueError):
            writer._stop("writer")
        assert not any(row[0] == "stop" for row in calls)
        assert writer.exit_observations[0]["error"] is not None
    else:
        result = writer._stop("writer")
        assert calls == [
            ("container", "inspect", "writer"),
            ("stop", "--time", "90", "writer"),
            ("container", "inspect", "writer"),
        ]
        replay_writer_exits(
            {"original": writer.original, "exit_observations": writer.exit_observations},
            [result],
            budget=budget,
        )
