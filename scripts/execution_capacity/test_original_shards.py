"""Durable typed originals over private temporary files; no clean fixture authority."""

from decimal import Decimal
from uuid import UUID

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError


def test_durable_typed_shards_reopen_exact_shared_originals(tmp_path):
    from scripts.execution_capacity.original_shards import OriginalView, write_originals

    source = {
        "id": UUID(int=1),
        "amount": Decimal("0.123456789012345678901"),
        "raw": b"original\x00bytes",
    }
    root = tmp_path / "originals"
    roots = {
        "cleanup": {"source": source, "quiescence": {"source": source}},
        "operands": {},
        "objects": [],
        "transports": [],
        "sql": [],
    }
    manifest = write_originals(root, roots, {"protocol_id": "fixture"}, budget=EvidenceBudget())
    view = OriginalView.open(root, budget=EvidenceBudget())
    actual = view.materialize()
    assert actual["cleanup"]["source"] == source
    assert actual["cleanup"]["source"] is actual["cleanup"]["quiescence"]["source"]
    assert actual["objects"] == []
    assert view.manifest == manifest
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())


@pytest.mark.parametrize("fault", ["missing", "changed", "orphan"])
def test_shard_coverage_cannot_be_inferred_from_manifest_alone(tmp_path, fault):
    from scripts.execution_capacity.original_shards import OriginalView, write_originals

    root = tmp_path / "originals"
    roots = {
        "cleanup": {"private": "body"},
        "operands": {},
        "objects": [],
        "transports": [],
        "sql": [],
    }
    write_originals(root, roots, {"protocol_id": "fixture"}, budget=EvidenceBudget())
    shard = root / "000000.jsonl"
    if fault == "missing":
        shard.unlink()
    elif fault == "changed":
        shard.write_bytes(shard.read_bytes().replace(b"body", b"fake"))
    else:
        (root / "999999.jsonl").write_bytes(b"orphan")
    with pytest.raises(ValueError, match="original"):
        OriginalView.open(root, budget=EvidenceBudget())


def test_quota_write_keeps_partial_bytes_without_complete_manifest(tmp_path):
    from scripts.execution_capacity.original_shards import write_originals

    root = tmp_path / "originals"
    roots = {
        "cleanup": {"private": "x" * 10000},
        "operands": {},
        "objects": [],
        "transports": [],
        "sql": [],
    }
    with pytest.raises(EvidenceQuotaError):
        write_originals(
            root, roots, {"protocol_id": "fixture"}, budget=EvidenceBudget(bytes_limit=500)
        )
    assert not (root / "manifest.json").exists()


@pytest.mark.parametrize(
    "fault", ["self-cycle", "scalar-container", "boolean-node", "oversize-container"]
)
def test_valid_hash_does_not_authorize_invalid_graph(tmp_path, fault):
    import json
    from hashlib import sha256

    from scripts.execution_capacity.attempt import encode
    from scripts.execution_capacity.original_shards import OriginalView, write_originals

    root = tmp_path / "originals"
    roots = {"cleanup": {"body": "x"}, "operands": {}, "objects": [], "transports": [], "sql": []}
    manifest = write_originals(root, roots, {}, budget=EvidenceBudget())
    shard = root / "000000.jsonl"
    rows = [json.loads(line) for line in shard.read_bytes().splitlines()]
    if fault == "self-cycle":
        row = next(row for row in rows if row["kind"] == "member")
        row["child"] = row["node"]
    elif fault == "scalar-container":
        next(row for row in rows if row["kind"] == "value")["value"] = {"hidden": "container"}
    elif fault == "boolean-node":
        rows[0]["node"] = False
    else:
        rows[0]["length"] = 10**30
    data = b"".join(encode(row) + b"\n" for row in rows)
    shard.write_bytes(data)
    manifest["shards"][0].update(bytes=len(data), sha256=sha256(data).hexdigest())
    (root / "manifest.json").write_bytes(encode(manifest))
    with pytest.raises(ValueError, match=r"original|quota"):
        OriginalView.open(root, budget=EvidenceBudget()).materialize()
