"""Filesystem-only write-ahead ownership and interrupted recovery contracts."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from scripts.execution_capacity.ownership import OwnershipJournal, TargetIdentity, fixture_manifest


def target(**changes):
    return TargetIdentity(
        **{
            "environment": "test",
            "runtime_id": str(UUID(int=1)),
            "database_id": "capacity_db_1",
            "scope_id": "team:owned",
            **changes,
        }
    )


def manifest():
    return fixture_manifest(
        fixture_id=UUID(int=2),
        seed=7,
        window_end=datetime(2026, 9, 17, tzinfo=UTC),
        target=target(),
    )


def test_intent_is_durable_before_creation_and_resume_has_exact_ids(tmp_path):
    root = tmp_path / "fixture"
    operation = UUID(int=3)
    with OwnershipJournal.create(root, manifest()) as journal:
        journal.intent(operation, "run", expected_id=str(UUID(int=4)))
    with OwnershipJournal.open(root, target()) as resumed:
        assert resumed.pending() == [str(operation)]
        with pytest.raises(ValueError, match="identity"):
            resumed.created(operation, "foreign")
        resumed.created(operation, str(UUID(int=4)))
        resumed.begin_cleanup(operation)
        resumed.retained(operation, proof_digest="a" * 64)
    with OwnershipJournal.open(root, target()) as resumed:
        assert resumed.pending() == []
        assert resumed.resources[str(operation)]["status"] == "retained"
        assert resumed.resources[str(operation)]["resource_id"] == str(UUID(int=4))
        with pytest.raises(ValueError, match="immutable"):
            resumed.deleted(operation, proof_digest="b" * 64)


def test_server_assigned_ids_and_quarantine_cannot_disappear(tmp_path):
    with OwnershipJournal.create(tmp_path / "fixture", manifest()) as journal:
        operation = UUID(int=5)
        journal.intent(operation, "environment")
        journal.created(operation, "native-environment-id")
        journal.begin_cleanup(operation)
        journal.quarantine(operation, proof_digest="c" * 64)
        assert journal.pending() == [str(operation)]
        with pytest.raises(ValueError, match="transition"):
            journal.deleted(operation, proof_digest="d" * 64)
        assert journal.resources[str(operation)]["status"] == "quarantined"
        with pytest.raises(ValueError, match="unknown"):
            journal.begin_cleanup(UUID(int=99))


def test_foreign_runtime_and_production_refused(tmp_path):
    with pytest.raises(ValueError, match="test"):
        target(environment="production")
    root = tmp_path / "fixture"
    with OwnershipJournal.create(root, manifest()):
        pass
    with pytest.raises(ValueError, match="target"):
        OwnershipJournal.open(root, target(database_id="another_db"))


def test_torn_journal_is_preserved_and_never_claimed_clean(tmp_path):
    root = tmp_path / "fixture"
    with OwnershipJournal.create(root, manifest()) as journal:
        journal.intent(UUID(int=3), "batch")
    path = root / "ownership.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"partial":')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="incomplete"):
        OwnershipJournal.open(root, target())
    assert path.read_bytes() == before


def test_journal_rejects_symlinks_and_second_writer(tmp_path):
    root = tmp_path / "fixture"
    with OwnershipJournal.create(root, manifest()), pytest.raises(ValueError, match="busy"):
        OwnershipJournal.open(root, target())
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        OwnershipJournal.open(linked, target())


def test_dependencies_prevent_parent_cleanup_before_owned_child(tmp_path):
    with OwnershipJournal.create(tmp_path / "fixture", manifest()) as journal:
        parent, child = UUID(int=3), UUID(int=4)
        journal.intent(parent, "team")
        journal.created(parent, "team-native")
        journal.intent(child, "batch", parents=[parent])
        journal.created(child, "batch-native")
        with pytest.raises(ValueError, match="dependent"):
            journal.begin_cleanup(parent)
        journal.begin_cleanup(child)
        journal.retained(child, proof_digest="a" * 64)
        journal.begin_cleanup(parent)


def test_manifest_counts_are_shared_and_not_an_accepted_report():
    from scripts.acceptance.capacity import FIXTURE_COUNTS

    result = manifest()
    assert result["counts"] == FIXTURE_COUNTS
    assert result["scope_ids"] == ["team:owned"]
    assert result["status"] == "planned"
    assert "accepted" not in result


def test_repeated_exact_cleanup_ack_is_idempotent_but_changed_proof_is_not(tmp_path):
    with OwnershipJournal.create(tmp_path / "fixture", manifest()) as journal:
        operation = UUID(int=3)
        journal.intent(operation, "run", expected_id="owned-run")
        journal.intent(operation, "run", expected_id="owned-run")
        journal.created(operation, "owned-run")
        journal.created(operation, "owned-run")
        journal.begin_cleanup(operation)
        journal.begin_cleanup(operation)
        journal.retained(operation, proof_digest="a" * 64)
        before = (journal.root / "ownership.jsonl").read_bytes()
        journal.retained(operation, proof_digest="a" * 64)
        assert (journal.root / "ownership.jsonl").read_bytes() == before
        with pytest.raises(ValueError, match="transition"):
            journal.retained(operation, proof_digest="b" * 64)


def test_fsync_failure_poisoning_prevents_claiming_clean_or_further_writes(tmp_path, monkeypatch):
    import os

    with OwnershipJournal.create(tmp_path / "fixture", manifest()) as journal:

        def fail(_):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(OSError, match="simulated disk failure"):
            journal.intent(UUID(int=3), "batch")
        with pytest.raises(ValueError, match="uncertain"):
            journal.pending()
        with pytest.raises(ValueError, match="uncertain"):
            journal.intent(UUID(int=4), "batch")


def test_journal_tampering_is_not_silently_recovered(tmp_path):
    root = tmp_path / "fixture"
    with OwnershipJournal.create(root, manifest()) as journal:
        journal.intent(UUID(int=3), "batch", expected_id="owned")
    path = root / "ownership.jsonl"
    path.write_bytes(path.read_bytes().replace(b"owned", b"other"))
    with pytest.raises(ValueError, match="integrity"):
        OwnershipJournal.open(root, target())
