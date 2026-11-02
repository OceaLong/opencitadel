"""Closed original export exercised only with private files and real local ledgers."""

import pytest
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.evidence_bounds import EvidenceBudget


def test_cleanup_prefix_is_fixed_before_later_phase_append(tmp_path):
    from scripts.execution_capacity.c2c_export import (
        close_originals,
        snapshot_cleanup,
        verify_export,
    )

    config = {
        "seal_id": "fixture-seal",
        "protocol_id": "fixture-protocol",
        "identity": {"attempt_id": "fixture"},
        "evidence_root": str(tmp_path),
    }
    from scripts.execution_capacity.attempt import digest

    plan = {
        "protocol_id": config["protocol_id"],
        "identity": config["identity"],
        "config_digest": digest(config),
    }
    with AttemptLedger.create(tmp_path / "operations", plan) as ledger:
        ledger.append("phase-intent", {"phase": "cleanup"})
        cleanup = {
            "source_inventory_digest": digest({}),
            "writer_quiescence_digest": digest({}),
            "complete_quiescence_digest": digest({}),
            "observer_closed_ns": 1,
        }
        roots = {"cleanup": cleanup, "operands": {}, "objects": [], "transports": [], "sql": []}
        binding = close_originals(tmp_path, config, ledger, roots, budget=EvidenceBudget())
        ledger.append("phase-complete", {"phase": "cleanup"})
        receipt = snapshot_cleanup(tmp_path, ledger, binding, budget=EvidenceBudget())
        prior = (tmp_path / "c2c-ledger" / "attempt.jsonl").read_bytes()
        ledger.append("phase-intent", {"phase": "stop"})
        assert (tmp_path / "c2c-ledger" / "attempt.jsonl").read_bytes() == prior
        view, original = verify_export(
            tmp_path,
            receipt,
            protocol_id="fixture-protocol",
            config_digest=digest(config),
            identity=config["identity"],
            budget=EvidenceBudget(),
        )
        assert original.root == tmp_path / "operations"
        assert original.count("c2c-private-final") == 1
        assert not any(r["body"].get("phase") == "stop" for r in original.rows)
        assert view.materialize()["cleanup"] == cleanup
        with pytest.raises(ValueError, match="protocol"):
            verify_export(
                tmp_path,
                receipt,
                protocol_id="other",
                config_digest=digest(config),
                identity=config["identity"],
                budget=EvidenceBudget(),
            )
        path = tmp_path / "c2c-ledger" / "attempt.jsonl"
        path.write_bytes(prior + b"{}\n")
        with pytest.raises(ValueError, match=r"original|artifact"):
            verify_export(
                tmp_path,
                receipt,
                protocol_id="fixture-protocol",
                config_digest=digest(config),
                identity=config["identity"],
                budget=EvidenceBudget(),
            )


@pytest.mark.parametrize(
    "name",
    [
        "../cleanup",
        "c2c-shard-0",
        "c2c-shard-0000000",
        "c2c-shard--00001",
        "c2c-shard-999999",
        "c2c-unlisted",
    ],
)
def test_closed_artifact_name_and_manifest_ordinal(tmp_path, name):
    from scripts.execution_capacity.c2c_export import artifact_path

    with pytest.raises(ValueError, match=r"original|artifact"):
        artifact_path(tmp_path, name)


def test_private_incremental_encoding_matches_legacy_without_deepcopy(tmp_path, monkeypatch):
    from dataclasses import dataclass

    from scripts.execution_capacity import guest_seal
    from scripts.execution_capacity.attempt import digest, encode
    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

    @dataclass
    class Value:
        rows: list

    value = Value([{"text": "é", "n": 1}])
    monkeypatch.setattr(guest_seal, "plain", lambda _: pytest.fail("eager plain must not run"))
    receipt = guest_seal.write_private(tmp_path / "value.json", value, budget=EvidenceBudget())
    assert (tmp_path / "value.json").read_bytes() == encode({"rows": value.rows})
    assert receipt["sha256"] == digest({"rows": value.rows})
    with pytest.raises(EvidenceQuotaError):
        guest_seal.write_private(
            tmp_path / "quota.json", Value(["x" * 10000]), budget=EvidenceBudget(bytes_limit=100)
        )
    assert not (tmp_path / "quota.json").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_actual_base_closeout_syncs_new_directory_parent_before_final(tmp_path, monkeypatch, fail):
    from scripts.execution_capacity import original_shards
    from scripts.execution_capacity.attempt import digest
    from scripts.execution_capacity.c2c_export import close_originals

    config = {"seal_id": "fixture", "protocol_id": "fixture", "identity": {"attempt_id": "fixture"}}
    plan = {
        "identity": config["identity"],
        "protocol_id": "fixture",
        "config_digest": digest(config),
    }
    calls = []
    real_sync = original_shards._sync_directory
    with AttemptLedger.create(tmp_path / "operations", plan) as ledger:

        def sync(path):
            assert not ledger.records("c2c-private-final")
            calls.append(path)
            if fail and path == tmp_path:
                raise OSError("fixture parent fsync")
            return real_sync(path)

        monkeypatch.setattr(original_shards, "_sync_directory", sync)
        cleanup = {
            "source_inventory_digest": digest({}),
            "writer_quiescence_digest": digest({}),
            "complete_quiescence_digest": digest({}),
            "observer_closed_ns": 1,
        }
        roots = {"cleanup": cleanup, "operands": {}, "objects": [], "transports": [], "sql": []}
        if fail:
            with pytest.raises(OSError, match="parent fsync"):
                close_originals(tmp_path, config, ledger, roots, budget=EvidenceBudget())
            assert not ledger.records("c2c-private-final")
            assert (tmp_path / "c2c-originals").is_dir()
        else:
            close_originals(tmp_path, config, ledger, roots, budget=EvidenceBudget())
            assert calls == [tmp_path / "c2c-originals", tmp_path]
            assert ledger.count("c2c-private-final") == 1


def test_fetch_export_exhaustion_precedes_manifest_parse(tmp_path, monkeypatch):
    import base64
    import json
    from hashlib import sha256
    from types import SimpleNamespace

    from scripts.execution_capacity.c2c_export import fetch_export
    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

    raw = b'{"shards":[]}'
    budget = EvidenceBudget(bytes_limit=3 * len(raw))

    class Session:
        ledger = SimpleNamespace(root=tmp_path)

        def seal_phase(self, phase, *, artifact, offset):
            return {
                "artifact": artifact,
                "offset": offset,
                "size_bytes": len(raw),
                "data": base64.b64encode(raw).decode(),
                "eof": True,
            }

    monkeypatch.setattr(
        json, "loads", lambda *args, **kwargs: pytest.fail("exhausted unit reached parser")
    )
    with pytest.raises(EvidenceQuotaError):
        fetch_export(
            Session(),
            {"final": {"manifest": {"size_bytes": len(raw), "sha256": sha256(raw).hexdigest()}}},
            budget=budget,
        )


def test_owned_v2_export_fetches_actual_raw_files_and_reopens_fresh(tmp_path):
    import base64
    from types import SimpleNamespace
    from uuid import UUID

    from scripts.execution_capacity.attempt import digest
    from scripts.execution_capacity.c2c_export import (
        close_originals,
        fetch_export,
        snapshot_cleanup,
    )
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.guest_seal_entry import artifact_relative

    guest = tmp_path / "guest"
    guest.mkdir(mode=0o700)
    host = tmp_path / "host"
    host.mkdir(mode=0o700)
    config = {"seal_id": "fixture", "protocol_id": "fixture", "identity": {"attempt_id": "fixture"}}
    plan = {
        "identity": config["identity"],
        "protocol_id": "fixture",
        "config_digest": digest(config),
    }
    owner = EvidenceOwner(
        original_root=guest / "c2c-originals", index_bytes=256 * 1024, chunk_bytes=4096
    )
    shared = {"id": UUID(int=11)}
    cleanup = {
        "source_inventory_digest": digest({}),
        "writer_quiescence_digest": digest({}),
        "complete_quiescence_digest": digest({}),
        "observer_closed_ns": 1,
        "source": shared,
        "quiescence": {"source": shared},
    }
    try:
        for ordinal in range(25):
            owner.retain("projection", {"ordinal": ordinal, "data": "x" * 700})
        with AttemptLedger.create(guest / "operations", plan) as ledger:
            ledger.evidence_owner = owner
            ledger.append("phase-intent", {"phase": "cleanup"})
            final = close_originals(
                guest,
                config,
                ledger,
                {
                    "cleanup": cleanup,
                    "operands": owner.originals,
                    "sql": owner.sql_reads,
                    "objects": owner.journal.sequence("objects"),
                    "transports": owner.journal.sequence("transports"),
                },
                budget=owner.budget,
            )
            ledger.append("phase-complete", {"phase": "cleanup"})
            receipt = snapshot_cleanup(guest, ledger, final, budget=owner.budget)
    finally:
        owner.journal.close()

    class Session:
        identity = config["identity"]
        ledger = SimpleNamespace(
            root=host,
            plan={"seal": {"export": {"protocol_id": "fixture"}, "config_digest": digest(config)}},
        )

        def seal_phase(self, phase, *, artifact, offset):
            assert phase == "read"
            path = guest / artifact_relative(artifact)
            with path.open("rb") as stream:
                stream.seek(offset)
                raw = stream.read(128 * 1024)
            return {
                "artifact": artifact,
                "offset": offset,
                "size_bytes": path.stat().st_size,
                "data": base64.b64encode(raw).decode(),
                "eof": offset + len(raw) == path.stat().st_size,
            }

    view, original = fetch_export(
        Session(), receipt, budget=EvidenceBudget(), index_bytes=256 * 1024
    )
    with view:
        actual = view.materialize()["cleanup"]
        assert actual["source"] is actual["quiescence"]["source"]
        assert actual["source"]["id"] == UUID(int=11)
        assert original.count("c2c-private-final") == 1
        assert (host / "c2c-originals" / "000000.bin").read_bytes() == (
            guest / "c2c-originals" / "000000.bin"
        ).read_bytes()
