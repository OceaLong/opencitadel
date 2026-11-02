"""Immutable ledger/base joins with a substituted physical-round verifier."""

import copy
from types import SimpleNamespace

import pytest
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity import writer_base as source


def test_prior_writer_rows_need_same_seal_source_and_image_authority(monkeypatch):
    monkeypatch.setattr(
        source, "verify_round", lambda parent, child: SimpleNamespace(round_id="child")
    )
    payload = {
        "seal_id": "seal",
        "source_inventory_digest": canonical_digest({"actual": "base"}),
        "writers": {"old": {"body": {"boot_id": "old-boot"}, "receipt": {"resource_closed": True}}},
        "uploads": {"upload": {"body": {"writer_id": "old"}, "receipt": {"sha256": "a" * 64}}},
        "supervisors": {"old": {"body": {"reports": []}, "receipt": None}},
        "exits": [{"container_id": "old-container", "exited": True}],
        "errors": [],
    }
    expected = {
        "seal_id": "seal",
        "quiescence_digest": canonical_digest(payload),
        "source_inventory_digest": payload["source_inventory_digest"],
        "image_digest": "f" * 64,
    }
    parent = SimpleNamespace(plan={"writer_base": expected})
    child = SimpleNamespace(
        plan={"writer_base": expected, "vm": {"base_identity": {"sha256": "f" * 64}}}
    )
    inventory = SimpleNamespace(require_complete=lambda: None, safe=lambda: {"actual": "base"})
    value = source.SealedWriters.from_ledgers(payload, parent, child, inventory)
    assert value.writers == payload["writers"]
    for corrupt in ["missing_receipt", "changed_history", "wrong_image", "missing_binding"]:
        body, bad = copy.deepcopy(payload), copy.deepcopy(child)
        if corrupt == "missing_receipt":
            body["uploads"]["upload"]["receipt"] = None
        elif corrupt == "changed_history":
            body["writers"]["old"]["body"]["boot_id"] = "new-boot"
        elif corrupt == "wrong_image":
            bad.plan["vm"]["base_identity"]["sha256"] = "0" * 64
        else:
            del bad.plan["writer_base"]
        with pytest.raises(ValueError, match=r"authority missing|linkage differs"):
            source.SealedWriters.from_ledgers(body, parent, bad, inventory)


def test_final_reader_preserves_inherited_rows_with_new_boot_same_hostname(tmp_path):
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.writer_lifecycle import ContainerWriters

    tmp_path.chmod(0o700)
    # The separate from_ledgers test verifies base authority; this isolates exact
    # after-exit journal union with no Docker/database effects.
    with RecoveryJournal(tmp_path) as journal:
        for identity, boot in [("old", "base-boot"), ("new", "clone-boot")]:
            journal.intent("writer", identity, {"hostname": "same", "boot_id": boot})
            journal.acknowledge("writer", identity, {"resource_closed": True})
            journal.intent("writer_supervisor", identity, {"reports": []})
        old = journal.get("writer", "old")
        supervisor = journal.get("writer_supervisor", "old")
    writers = ContainerWriters.__new__(ContainerWriters)
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    writers.budget = EvidenceBudget()
    writers.root, writers.original = tmp_path, {"current-container": {}}
    writers.exits = {"current-container": {"exited": True}}
    writers.exit_observations = []  # This fixture isolates journal union, not physical exit replay.
    writers.writer_ids, writers.prior_ids = {"new": "current-container"}, {"old"}
    writers.base = SimpleNamespace(
        writers={"old": old}, supervisors={"old": supervisor}, uploads={}
    )
    assert not writers.final_journals()["issues"]
    with RecoveryJournal(tmp_path) as journal:
        # Deliberate local-corruption transcript, never product recovery logic.
        journal.db.execute("UPDATE intents SET body='{}' WHERE kind='writer' AND identity='old'")
        journal.db.commit()
    assert any(r["kind"] == "base-history" for r in writers.final_journals()["issues"])


def test_final_journal_reader_does_not_create_missing_history(tmp_path):
    import sqlite3

    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal, RecoveryJournal

    tmp_path.chmod(0o700)
    with pytest.raises(FileNotFoundError):
        ReadOnlyRecoveryJournal(tmp_path)
    assert not (tmp_path / "recovery.sqlite3").exists()
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("writer", "old", {"boot": "old"})
    before = (tmp_path / "recovery.sqlite3").read_bytes()
    with ReadOnlyRecoveryJournal(tmp_path) as journal:
        assert journal.get("writer", "old")["body"] == {"boot": "old"}
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            journal.db.execute("DELETE FROM intents")
    assert (tmp_path / "recovery.sqlite3").read_bytes() == before


@pytest.mark.parametrize(
    "mutation", ["none", "new_current", "extra_inherited", "missing", "changed", "relabelled"]
)
def test_inherited_upload_set_is_exact_and_only_current_writers_can_add(tmp_path, mutation):
    from scripts.execution_capacity.observers import RecoveryJournal
    from scripts.execution_capacity.writer_lifecycle import ContainerWriters

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        for identity, boot in [("old", "base-boot"), ("new", "current-boot")]:
            journal.intent("writer", identity, {"hostname": "same-host", "boot_id": boot})
            journal.acknowledge("writer", identity, {"resource_closed": True})
            journal.intent("writer_supervisor", identity, {"reports": []})
        journal.intent("sdk_upload", "sealed-upload", {"writer_id": "old", "key": "sealed-key"})
        journal.acknowledge("sdk_upload", "sealed-upload", {"sha256": "a" * 64})
        base = SimpleNamespace(
            writers={"old": journal.get("writer", "old")},
            supervisors={"old": journal.get("writer_supervisor", "old")},
            uploads={"sealed-upload": journal.get("sdk_upload", "sealed-upload")},
        )
        if mutation in {"new_current", "extra_inherited"}:
            journal.intent(
                "sdk_upload",
                "added-after-base",
                {"writer_id": "new" if mutation == "new_current" else "old", "key": "new-key"},
            )
            journal.acknowledge("sdk_upload", "added-after-base", {"sha256": "b" * 64})
        elif mutation == "missing":
            journal.db.execute("DELETE FROM intents WHERE kind='sdk_upload'")
        elif mutation in {"changed", "relabelled"}:
            journal.db.execute(
                "UPDATE intents SET body=? WHERE kind='sdk_upload'",
                (
                    '{"writer_id":"new","key":"sealed-key"}'
                    if mutation == "relabelled"
                    else '{"writer_id":"old","key":"rewritten"}',
                ),
            )
        journal.db.commit()
    writers = ContainerWriters.__new__(ContainerWriters)
    from scripts.execution_capacity.evidence_bounds import EvidenceBudget

    writers.budget = EvidenceBudget()
    writers.root, writers.original = tmp_path, {"container": {}}
    writers.exits = {"container": {"exited": True}}
    writers.exit_observations = []  # Physical exit observations belong to separate lifecycle tests.
    writers.writer_ids, writers.prior_ids = {"new": "container"}, {"old"}
    writers.base = base
    result = writers.final_journals()
    assert bool(result["issues"]) == (mutation not in {"none", "new_current"})
    if mutation == "extra_inherited":
        assert "added-after-base" in result["uploads"]  # retain the invalid original row
