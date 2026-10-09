"""A finite shard contains only emitted rows, with exact role field closure."""

import hashlib
import json

import pytest
from capacity_population_writer import FiniteRoleWriter


def test_finite_role_writer_keeps_per_family_order_and_exact_shard_inventory(tmp_path):
    writer = FiniteRoleWriter(
        tmp_path,
        "workload",
        controls={
            "schema_version": 3,
            "attempt_id": "attempt",
            "protocol_id": "protocol",
            "role": "workload",
        },
        list_fields=("windows", "progress", "source_acks", "errors"),
    )
    writer.start("windows")
    writer.append("windows", {"window_id": "first"})
    writer.append("windows", {"window_id": "second"})
    writer.finish_field("windows")
    writer.start("progress")
    writer.append("progress", {"progress_id": "p1"})
    writer.finish_field("progress")
    descriptors = writer.close()
    assert len(descriptors) == 2
    assert [row["shard_index"] for row in descriptors] == [0, 1]
    assert all(row["shard_count"] == 2 for row in descriptors)
    shards = []
    for descriptor in descriptors:
        path = tmp_path / descriptor["path"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        assert len(raw) == descriptor["size_bytes"]
        assert path.stat().st_mode & 0o777 == 0o600
        shards.append(json.loads(raw))
    assert [row["window_id"] for row in shards[0]["windows"]] == ["first", "second"]
    assert shards[0]["progress"] == []
    assert shards[1]["windows"] == []
    assert [row["progress_id"] for row in shards[1]["progress"]] == ["p1"]
    assert shards[0]["errors"] == shards[1]["errors"] == []
    assert {key: value for key, value in shards[0].items() if key not in writer.fields} == {
        key: value for key, value in shards[1].items() if key not in writer.fields
    }


def test_finite_role_writer_refuses_wrong_field_and_unfinished_family(tmp_path):
    writer = FiniteRoleWriter(
        tmp_path,
        "c2c",
        controls={"schema_version": 3, "attempt_id": "a", "protocol_id": "p", "role": "c2c"},
        list_fields=("units",),
    )
    writer.start("units")
    with pytest.raises(ValueError, match="owner"):
        writer.append("other", {"unit": "foreign"})
    with pytest.raises(ValueError, match="active family"):
        writer.close()
    writer.finish_field("units")
    with pytest.raises(ValueError, match="empty sharded role"):
        writer.close()
