"""Fixed round-child diagnostic/native originals and exact public Query binding."""

import os
import stat

from scripts.acceptance.capacity_diagnostics import validate_query
from scripts.acceptance.capacity_io import canonical_digest, strict_json
from scripts.acceptance.capacity_models import Measurements, Plan
from scripts.execution_capacity.c2c_export import _receipt
from scripts.execution_capacity.pg_diagnostics import authorize_request
from scripts.execution_capacity.pg_diagnostics_timed import diagnostic_inventory, replay_original
from scripts.execution_capacity.proof_copy import directory


def round_diagnostics(
    parent, child, binding, origin, inventory, originals, *, budget, base_inventory=None
):
    """Only the actual verified child owns dispatch/result; parent is never fallback."""
    dispatches = child.records("pg-diagnostics-dispatch")
    receipts = child.records("pg-diagnostics-result")
    commands = [row["request"]["command_id"] for row in originals]
    if (
        len(set(commands)) != len(commands)
        or sorted(commands) != sorted(row["body"]["command_id"] for row in dispatches)
        or sorted(commands) != sorted(row["body"]["command_id"] for row in receipts)
    ):
        raise ValueError("complete original round diagnostic commands required")
    if any(
        row["body"].get("command_id") in commands
        for kind in ("pg-diagnostics-dispatch", "pg-diagnostics-result")
        for row in parent.records(kind)
    ):
        raise ValueError("competing parent diagnostic command identity")
    if not commands:
        return [], {}
    completed = child.records("native-complete-observed")
    if len(completed) != 1:
        raise ValueError("original native completion required for diagnostics")
    completion = completed[0]["body"]
    relative = "native-" + binding.window_id + ".json"
    if (
        completion["artifact"] != relative
        or completion["round_binding"] != binding.model_dump()
        or completion["identity"]["attempt_id"] != binding.round_id
        or completion["identity"]["sample_id"] != binding.sample_id
        or completion["identity"]["window_id"] != binding.window_id
    ):
        raise ValueError("original diagnostic native identity differs")
    with directory(child.location) as parent_fd:
        fd = os.open(relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size > 32 * 1024**2
            ):
                raise ValueError("private bounded diagnostic native original required")
            budget.reserve(before.st_size * 64 + 1, rows=before.st_size, largest=before.st_size)
            raw = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if len(raw) != before.st_size or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("diagnostic native original changed")
    document = strict_json(raw)
    if canonical_digest(document) != completion["digest"]:
        raise ValueError("original native completion commitment differs")
    measurements = Measurements.model_validate(document)
    if (
        measurements.attempt_id != binding.parent_attempt_id
        or measurements.protocol_id != child.plan["protocol_id"]
        or measurements.errors
    ):
        raise ValueError("original diagnostic native parent differs")
    plans = [
        Plan.model_validate(row)
        for row in parent.plan["samples"]
        if row["sample_id"] == binding.sample_id
    ]
    samples = [row for row in measurements.samples if row.sample_id == binding.sample_id]
    if (
        len(plans) != 1
        or len(samples) != 1
        or plans[0].physical_window_id != binding.window_id
        or samples[0].status != "ok"
        or samples[0].error is not None
        or samples[0].end_ns > completion["host_ns"]
    ):
        raise ValueError("actual diagnostic plan/sample missing")
    queries = []
    for original in originals:
        selected = diagnostic_inventory(original, inventory, base_inventory, budget=budget)
        result = replay_original(original, selected, budget=budget)
        expected = authorize_request(
            child, plans[0], samples[0], origin, command_id=result.request.command_id
        )
        if result.request != expected:
            raise ValueError("original diagnostic request differs")
        query = result.export(child, budget=budget)
        sources = [
            source
            for source in measurements.sources
            if source.sample_id == query.sample_id and source.source_id == samples[0].source_id
        ]
        if len(sources) != 1:
            raise ValueError("original diagnostic repository source absent")
        validate_query(
            query,
            plans[0],
            samples[0],
            clock_id=samples[0].clock_id,
            origin=origin,
            source=sources[0],
        )
        queries.append(query)
    receipt = _receipt(child.location / relative, budget)
    from hashlib import sha256

    if receipt["sha256"] != sha256(raw).hexdigest():
        raise ValueError("native original changed after diagnostic replay")
    return queries, {relative: receipt}
