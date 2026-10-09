"""Partial public artifacts are emitted with bounded role-local shards."""

import hashlib
import json

from capacity_population import slots
from capacity_population_public import write_public_sharded_roles
from scripts.acceptance.capacity_models import Measurements, Resets, Workload


def test_partial_public_shards_keep_two_distinct_windows_and_complete_rows(tmp_path):
    all_slots = list(slots())
    warm = next(row for row in all_slots if row.mode == "warm")
    cold = next(row for row in all_slots if row.mode == "cold")

    def selected():
        return iter((warm, cold))

    descriptors = write_public_sharded_roles(tmp_path, slot_factory=selected)
    by_role = {role: [] for role in ("workload", "measurements", "resets")}
    for descriptor in descriptors:
        raw = (tmp_path / descriptor["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        assert len(raw) == descriptor["size_bytes"]
        role = descriptor["role"]
        parsed = json.loads(raw)
        {"workload": Workload, "measurements": Measurements, "resets": Resets}[role].model_validate(
            parsed
        )
        by_role[role].append(parsed)
    assert sum(len(row["windows"]) for row in by_role["workload"]) == 2
    assert sum(len(row["progress"]) for row in by_role["workload"]) == 1200
    assert sum(len(row["source_acks"]) for row in by_role["workload"]) == 1200
    assert sum(len(row["markers"]) for row in by_role["measurements"]) == 162
    assert sum(len(row["live_paints"]) for row in by_role["measurements"]) == 1200
    assert sum(len(row["samples"]) for row in by_role["measurements"]) == 2
    assert sum(len(row["sources"]) for row in by_role["measurements"]) == 2
    assert sum(len(row["browsers"]) for row in by_role["measurements"]) == 2
    assert sum(len(row["resources"]) for row in by_role["measurements"]) == 2
    assert sum(len(row["resets"]) for row in by_role["resets"]) == 1
