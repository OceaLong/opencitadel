"""Closed small cleanup wire references actual private original roots."""

from copy import deepcopy

import pytest
from scripts.execution_capacity.c2c_export import _receipt
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.original_journal import OriginalJournal
from scripts.execution_capacity.original_shards import OriginalView


@pytest.mark.parametrize(
    "mutation", [None, "version", "extra", "root", "foreign", "manifest", "closed"]
)
def test_cleanup_envelope_resolves_only_exact_live_original_root(tmp_path, mutation):
    from scripts.execution_capacity.cleanup_envelope import (
        cleanup_envelope,
        resolve_cleanup_envelope,
    )

    budget = EvidenceBudget()
    root = tmp_path / "originals"
    binding = {
        "protocol_id": "protocol",
        "config_digest": "config",
        "identity": {"source_digest": "source"},
        "prior": {},
        "unit": {"kind": "base"},
    }
    with OriginalJournal.create(root, budget=budget, index_bytes=512 * 1024) as owner:
        parent = owner.begin("operand:query-rows", {})
        writer = owner.begin_collection(parent, "rows")
        writer.append({"value": 1})
        rows = writer.complete()
        owner.complete(parent, rows)
        owner.append("cleanup", {"quiescence": {"rows": rows}})
        owner.seal(binding, original_roots=True)
    final = {"manifest": _receipt(root / "manifest.json", budget), **binding}
    wire = cleanup_envelope(final, budget=budget)
    assert set(wire) == {"schema", "root", "final"}
    with OriginalView.open(root, budget=budget, index_bytes=512 * 1024) as view:
        changed = deepcopy(wire)
        if mutation == "version":
            changed["schema"] = 1
        elif mutation == "extra":
            changed["quiescence"] = {"rows": []}
        elif mutation == "root":
            changed["root"]["ordinal"] = True
        elif mutation == "foreign":
            changed["final"]["identity"] = {"source_digest": "foreign"}
        elif mutation == "manifest":
            changed["final"]["manifest"]["sha256"] = "0" * 64
        elif mutation == "closed":
            view.close()
        if mutation is None:
            result = resolve_cleanup_envelope(changed, view=view, final=final, budget=budget)
            assert list(result["quiescence"]["rows"]) == [{"value": 1}]
        else:
            with pytest.raises(ValueError, match=r"envelope|closed"):
                resolve_cleanup_envelope(changed, view=view, final=final, budget=budget)


@pytest.mark.parametrize("fault", [None, "symlink-parent", "existing-file", "quota"])
def test_native_guest_fetch_uses_anchored_private_namespace(tmp_path, fault):
    import base64
    from hashlib import sha256
    from types import SimpleNamespace

    from scripts.execution_capacity.guest_seal_entry import artifact_relative
    from scripts.execution_capacity.seal_finalizer import fetch_private

    logical = "c2c-native-000000-" + "a" * 32 + "-manifest"
    root = tmp_path / "host"
    root.mkdir(mode=0o700)
    path = root / artifact_relative(logical)
    data = b"private native original"
    calls = []

    def page(phase, *, artifact, offset):
        calls.append((phase, artifact, offset))
        return {
            "artifact": artifact,
            "offset": offset,
            "size_bytes": len(data),
            "eof": True,
            "data": base64.b64encode(data).decode(),
        }

    session = SimpleNamespace(ledger=SimpleNamespace(root=root), seal_phase=page)
    if fault == "symlink-parent":
        (root / "c2c-originals").mkdir(mode=0o700)
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        (root / "c2c-originals" / "native-000000").symlink_to(outside, target_is_directory=True)
    elif fault == "existing-file":
        path.parent.mkdir(mode=0o700, parents=True)
        path.write_bytes(b"retained predecessor")
    budget = EvidenceBudget(bytes_limit=1 if fault == "quota" else 1024 * 1024)
    receipt = {"sha256": sha256(data).hexdigest(), "size_bytes": len(data)}
    if fault is None:
        assert fetch_private(session, logical, receipt, budget=budget) == path
        assert path.read_bytes() == data
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    else:
        with pytest.raises((OSError, ValueError)):
            fetch_private(session, logical, receipt, budget=budget)
        assert calls == []
        if fault == "symlink-parent":
            assert list(outside.iterdir()) == []
        elif fault == "existing-file":
            assert path.read_bytes() == b"retained predecessor"
