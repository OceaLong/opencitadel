"""Public load order/schema/lifetime tests, not full semantic capacity fixtures."""

import hashlib
import json
from dataclasses import fields

import pytest
from scripts.acceptance.capacity_io import load_artifacts
from scripts.acceptance.capacity_models import Artifact
from scripts.acceptance.capacity_package import PackageSession, PublicConsumerResources, PublicRows
from scripts.acceptance.capacity_role_rows import ROLE_ROWS
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.test_public_package_fixture import minimal_public_roles


def write_package(root):
    roles = minimal_public_roles()
    artifacts = []
    for name, value in roles.items():
        copies = [value]
        if name == "measurements":
            copies = [{**value, "errors": ["a", "b"]}, {**value, "errors": ["c"]}]
        for index, raw in enumerate(copies):
            data = json.dumps(raw).encode()
            path = f"{name}-{index}.json"
            (root / path).write_bytes(data)
            artifacts.append(
                Artifact(
                    path=path,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                    role=name,
                    schema_version=raw["schema_version"],
                    shard_index=index,
                    shard_count=len(copies),
                )
            )
    return artifacts


def resources():
    return PublicConsumerResources(EvidenceBudget(bytes_limit=64 * 1024 * 1024), 1024 * 1024)


def test_package_exact_old_fields_order_slice_and_closed_lifetime(tmp_path):
    artifacts = write_package(tmp_path)
    artifacts.reverse()
    old, _ = load_artifacts(artifacts, tmp_path)
    with PackageSession(artifacts, tmp_path, resources=resources()) as session:
        actual = session.roles
        assert set(actual) == set(old)
        for name, model in session.models.items():
            assert {f.name for f in fields(ROLE_ROWS[name]) if not f.name.startswith("_")} == set(
                model.model_fields
            )
            assert actual[name] == old[name]
            assert session.owns_role(actual[name], name)
        rows = actual["measurements"].errors
        assert type(rows) is PublicRows
        assert list(rows) == ["c", "a", "b"]
        assert rows[-1] == "b"
        reverse = rows[::-1]
        assert type(reverse) is PublicRows
        assert list(reverse) == ["b", "a", "c"]
        assert list(rows[2:2]) == []
        with pytest.raises(ValueError, match="zero"):
            _ = rows[::0]
        assert actual["environment"].model_dump() == old["environment"].model_dump()
    with pytest.raises(ValueError, match="closed"):
        _ = actual["measurements"].protocol_id
    with pytest.raises(ValueError, match="closed"):
        _ = reverse[0]


def test_late_bad_shard_closes_entire_unpublished_session(tmp_path, monkeypatch):
    from scripts.acceptance.capacity_index import CapacityIndex

    artifacts = write_package(tmp_path)
    last = artifacts[-1]
    (tmp_path / last.path).write_bytes(b"{}")
    closed = []
    original = CapacityIndex.close

    def close(index):
        closed.append(index.root)
        original(index)

    monkeypatch.setattr(CapacityIndex, "close", close)
    with pytest.raises(ValueError, match="digest mismatch"):
        PackageSession(artifacts, tmp_path, resources=resources())
    assert closed
    assert all(not path.exists() for path in closed)


def test_source_reopen_cannot_reuse_prior_digest(tmp_path):
    artifacts = write_package(tmp_path)
    with PackageSession(artifacts, tmp_path, resources=resources()) as session:
        source = artifacts[0]
        assert session.read_source(source) == (tmp_path / source.path).read_bytes()
        (tmp_path / source.path).write_bytes(b"{}")
        with pytest.raises(ValueError, match="changed after ingestion"):
            session.read_source(source)
