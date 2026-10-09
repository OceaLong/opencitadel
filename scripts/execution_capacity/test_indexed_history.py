"""Actual private journal unions retain every source before fixed-key merging."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.final_inventory import CumulativeJournal
from scripts.execution_capacity.inventory_reader import ReadOnlyParents
from scripts.execution_capacity.observers import RecoveryJournal
from scripts.execution_capacity.original_collections import CollectionRows


@pytest.mark.parametrize("fault", [None, "body", "receipt", "receipt-type"])
def test_cumulative_owned_union_orders_and_compares_all_source_occurrences(tmp_path, fault):
    from contextlib import ExitStack

    with ExitStack() as stack:
        owner = stack.enter_context(
            EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024)
        )
        owner.begin_cleanup()
        journals = []
        for ordinal in range(2):
            root = tmp_path / str(ordinal)
            root.mkdir(mode=0o700)
            journal = stack.enter_context(
                RecoveryJournal(root, budget=owner.budget.child(), index_bytes=512 * 1024)
            )
            journals.append(journal)
            for key in ("🙂", "a", "é"):
                journal.intent(
                    "run", key, {"id": key, "value": 2 if ordinal and fault == "body" else 1}
                )
            if ordinal or fault in ("receipt", "receipt-type"):
                journal.acknowledge(
                    "run",
                    "a",
                    True
                    if ordinal and fault == "receipt-type"
                    else ordinal + 1
                    if fault == "receipt"
                    else 1,
                )
        merged = CumulativeJournal(journals[:1], journals[1], budget=owner.budget, evidence=owner)
        if fault:
            with pytest.raises(ValueError, match="conflicting cumulative"):
                merged.records("run")
        else:
            rows = merged.records("run")
            assert type(rows) is CollectionRows
            assert [pair[0] for pair in rows] == ["a", "é", "🙂"]
            assert rows[0][1] == {"body": {"id": "a", "value": 1}, "receipt": 1}
            assert rows[1][1]["receipt"] is None
            assert merged.get("run", "missing") is None


def test_owned_parent_read_retains_finite_pairs_and_strict_receipt_absence(tmp_path):
    root = tmp_path / "native"
    root.mkdir(mode=0o700)
    with (
        EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as owner,
        RecoveryJournal(root, budget=owner.budget.child(), index_bytes=512 * 1024) as native,
    ):
        for key in ("c", "a", "b"):
            native.intent("run", key, {"key": key})
        parents = ReadOnlyParents((native,), evidence=owner, budget=owner.budget)
        result = parents.records("run")
        assert type(result) is CollectionRows
        assert [pair[0] for pair in result] == ["a", "b", "c"]
        saved = owner.originals["journal-read"][0]
        assert saved["operation"] == "records"
        assert saved["sequence"] == 1
        assert type(saved["value"]) is CollectionRows
        assert saved["value"][2] == ["c", {"body": {"key": "c"}, "receipt": None}]
        assert parents.get("run", "a") == {"body": {"key": "a"}, "receipt": None}
        assert parents.get("run", "missing") is None


def test_owned_history_native_transfer_closes_source_before_output_is_read(tmp_path):
    from scripts.execution_capacity.test_native_original_bodies import _native_fixture

    root, identity, expected = _native_fixture(tmp_path, collection=True)
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as owner:
        owner.begin_cleanup()
        with RecoveryJournal(root, budget=owner.budget.child(), index_bytes=512 * 1024) as native:
            merged = CumulativeJournal((), native, budget=owner.budget, evidence=owner)
            parents = ReadOnlyParents((merged,), budget=owner.budget, evidence=owner)
            rows = parents.records("environment_read")
        assert rows[0][0] == identity
        assert list(rows[0][1]["body"]["rows"]) == expected["rows"]
        assert rows[0][1]["body"]["rows"].owner is owner.journal


def test_finite_family_history_preserves_full_typed_rows_and_lease_lookup(tmp_path):
    from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
    from scripts.execution_capacity.replay_relations import same_value
    from scripts.execution_capacity.retained_final import RetainedHistory

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as owner:
        owner.begin_cleanup()
        records = {}
        first = {"at": 1}
        for family in (*HISTORY_FAMILIES, *PREDICATE_FAMILIES):
            writer = owner.journal.begin_dictionary(owner._cleanup_token, family)
            if family == "object":
                writer.append("b", {"body": {"id": "b"}, "receipt": None})
                writer.append("a", {"body": {"id": "a"}, "receipt": True})
            elif family == "environment_read":
                writer.append(
                    "read", {"body": {"batch_id": "batch", "reads": [first]}, "receipt": None}
                )
            records[family] = writer.complete()
        history = RetainedHistory(records, budget=owner.budget, owner=owner.journal)
        assert [key for key, _ in history.records("object")] == ["a", "b"]
        assert history.lease_read("batch", first)[0] == "read"
        assert same_value(
            records["object"],
            {
                "a": {"body": {"id": "a"}, "receipt": True},
                "b": {"body": {"id": "b"}, "receipt": None},
            },
            owner=owner.journal,
            budget=owner.budget,
        )
        assert not same_value(
            records["object"],
            {"a": {"body": {"id": "a"}, "receipt": 1}, "b": {"body": {"id": "b"}, "receipt": None}},
            owner=owner.journal,
            budget=owner.budget,
        )


def test_owned_family_snapshot_survives_native_close_and_uses_finite_original_maps(tmp_path):
    from scripts.execution_capacity.history_union import family_records
    from scripts.execution_capacity.original_dictionaries import DictionaryRows
    from scripts.execution_capacity.physical import PhysicalJournalSnapshot

    root = tmp_path / "native"
    root.mkdir(mode=0o700)
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as owner:
        owner.begin_cleanup()
        with RecoveryJournal(root, budget=owner.budget.child(), index_bytes=512 * 1024) as native:
            for key in ("z", "a"):
                native.intent("lease", key, {"id": key})
            merged = CumulativeJournal((), native, budget=owner.budget, evidence=owner)
            values = family_records(merged, "lease")
            snapshot = PhysicalJournalSnapshot.capture(merged)
        assert type(values) is DictionaryRows
        assert list(values) == ["a", "z"]
        assert [key for key, _ in snapshot.records("lease")] == ["a", "z"]
        assert list(snapshot.records("environment_observation")) == []
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(lambda: snapshot.records("lease")[0][1]["body"]).result() == {
                "id": "a"
            }
    with pytest.raises(ValueError, match=r"closed|invalid"):
        snapshot.records("lease")


def test_owned_history_rejects_missing_durable_cleanup_parent(tmp_path):
    root = tmp_path / "native"
    root.mkdir(mode=0o700)
    with (
        EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as owner,
        RecoveryJournal(root, budget=owner.budget, index_bytes=512 * 1024) as native,
    ):
        merged = CumulativeJournal((), native, budget=owner.budget, evidence=owner)
        with pytest.raises(ValueError, match="before acquisition"):
            merged.records("lease")


def test_finite_history_identity_order_preserves_lone_surrogate_keys(tmp_path):
    from scripts.acceptance.capacity_c2c_models import HISTORY_FAMILIES, PREDICATE_FAMILIES
    from scripts.execution_capacity.retained_final import RetainedHistory

    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as owner:
        owner.begin_cleanup()
        records = {}
        keys = ("🙂", "\ud800", "\x00", "a", "")
        for family in (*HISTORY_FAMILIES, *PREDICATE_FAMILIES):
            writer = owner.journal.begin_dictionary(owner._cleanup_token, family)
            if family == "object":
                for key in keys:
                    writer.append(key, {"body": {"key": key}, "receipt": None})
            records[family] = writer.complete()
        history = RetainedHistory(records, budget=owner.budget, owner=owner.journal)
        assert [key for key, _ in history.records("object")] == sorted(keys)
        assert history.get("object", "\ud800")["body"]["key"] == "\ud800"
