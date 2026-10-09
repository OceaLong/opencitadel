"""Finite dictionaries preserve original dict semantics without whole-map input."""

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.original_journal import OriginalJournal


def test_dictionary_producer_preserves_insertion_order_alias_and_fresh_reopen(tmp_path):
    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_dictionary(parent, "history")
        for key, value in (("é", {"value": 1}), ("a", {"value": True}), ("🙂", {"value": "1"})):
            writer.append(key, value)
        rows = writer.complete()
        assert list(rows) == ["é", "a", "🙂"]
        assert list(rows.sorted_keys()) == ["a", "é", "🙂"]
        assert rows["a"] == {"value": True}
        assert rows.get("absent") is None
        owner.complete(parent, {"one": rows, "alias": rows})
        owner.seal({})
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as fresh:
        value = fresh.sequence("operand:journal-read")[0]
        assert value["one"] is value["alias"]
        assert list(value["one"].items()) == [
            ("é", {"value": 1}),
            ("a", {"value": True}),
            ("🙂", {"value": "1"}),
        ]
    with pytest.raises(ValueError, match=r"closed|invalid"):
        len(value["one"])


@pytest.mark.parametrize("key", [1, True, b"key"])
def test_dictionary_producer_rejects_non_original_key_types(tmp_path, key):
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
    ) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_dictionary(parent, "history")
        with pytest.raises(ValueError, match="key"):
            writer.append(key, "value")


def test_dictionary_producer_rejects_duplicate_key_without_overwriting_prefix(tmp_path):
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
    ) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_dictionary(parent, "history")
        writer.append("same", {"value": 1})
        prefix = (owner.root / "occurrences.jsonl").read_bytes()
        with pytest.raises(ValueError, match="duplicate"):
            writer.append("same", {"value": 2})
        assert (owner.root / "occurrences.jsonl").read_bytes() == prefix
        assert not (owner.root / "manifest.json").exists()


def test_dictionary_entry_can_reference_completed_sibling_collection(tmp_path):
    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as owner:
        parent = owner.begin("operand:journal-read", {})
        child = owner.begin_collection(parent, "rows")
        child.append({"raw": 1})
        rows = child.complete()
        writer = owner.begin_dictionary(parent, "history")
        writer.append("body", {"one": rows, "alias": rows})
        values = writer.complete()
        owner.complete(parent, {"history": values})
        owner.seal({})
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as fresh:
        value = fresh.sequence("operand:journal-read")[0]["history"]["body"]
        assert value["one"] is value["alias"]
        assert list(value["one"]) == [{"raw": 1}]


def test_dictionary_full_typed_json_plain_oracles_and_fresh_copy(tmp_path):
    import json
    from decimal import Decimal
    from hashlib import sha256
    from uuid import UUID

    from scripts.acceptance.capacity_io import copy_artifacts
    from scripts.acceptance.capacity_models import Artifact
    from scripts.execution_capacity.evidence_json import json_digest
    from scripts.execution_capacity.original_plain import canonical_original_digest, plain_graph
    from scripts.execution_capacity.original_shards import OriginalView
    from scripts.execution_capacity.safe_projection import typed_digest

    expected = {
        "🙂": UUID(int=3),
        "é": Decimal("1.00"),
        "a\x00z": b"bytes",
        "a": True,
        "": [1, "1"],
    }
    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_dictionary(parent, "history")
        for key, value in expected.items():
            writer.append(key, value)
        rows = writer.complete()
        owner.complete(parent, {"map": rows})
        owner.append("cleanup", {"map": rows})
        owner.seal({}, original_roots=True)
    with OriginalView.open(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as view:
        value = view.materialize()["cleanup"]["map"]
        assert list(value.sorted_keys()) == ["", "a", "a\x00z", "é", "🙂"]
        commitment = typed_digest(value, budget=EvidenceBudget(), view=view)
        assert commitment == typed_digest(expected, budget=EvidenceBudget())
        assert json_digest(value, owner=view.journal, budget=view.budget) == json_digest(
            expected, budget=EvidenceBudget()
        )
        projected = plain_graph(value, owner=view.journal, budget=view.budget)
        oracle = json.loads(json.dumps(expected, default=str, sort_keys=True))
        assert list(projected) == list(oracle)
        assert (
            canonical_original_digest(projected, owner=view.journal, budget=view.budget)
            == sha256(
                json.dumps(oracle, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        descriptors = [
            Artifact(
                path=p.name,
                role="measurements",
                schema_version=3,
                sha256=sha256(p.read_bytes()).hexdigest(),
                size_bytes=p.stat().st_size,
            )
            for p in root.iterdir()
        ]
        copy_artifacts(descriptors, root, tmp_path / "copied")
    with OriginalView.open(
        tmp_path / "copied", budget=EvidenceBudget(), index_bytes=256 * 1024
    ) as fresh:
        copied = fresh.materialize()["cleanup"]["map"]
        assert typed_digest(copied, budget=EvidenceBudget(), view=fresh) == commitment
        with pytest.raises(ValueError, match="owner"):
            typed_digest(value, budget=EvidenceBudget(), view=fresh)


@pytest.mark.parametrize(
    "fault",
    ["count-bool", "list-kind", "missing-end", "edge-kind", "duplicate-entry", "late-entry"],
)
def test_dictionary_kind_count_and_occurrence_closure_are_not_digest_authority(tmp_path, fault):
    from scripts.execution_capacity.original_collections import CollectionRows
    from scripts.execution_capacity.original_dictionaries import DictionaryRows
    from scripts.execution_capacity.test_original_collections import _rewrite_log

    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_dictionary(parent, "history")
        writer.append("one", 1)
        if fault == "duplicate-entry":
            writer.append("two", 2)
        rows = writer.complete()
        if fault == "count-bool":
            forged = DictionaryRows(owner, rows.producer_ordinal, True, rows.body_descriptor)
            with pytest.raises(ValueError, match=r"count|snapshot|type"):
                len(forged)
            return
        if fault == "list-kind":
            forged = CollectionRows(owner, rows.producer_ordinal, rows.length, rows.body_descriptor)
            with pytest.raises(ValueError, match="kind"):
                len(forged)
            return
        if fault == "late-entry":
            with pytest.raises(ValueError, match=r"complete|closed|end"):
                writer.append("later", 2)
            return
        owner.complete(parent, {"map": rows})
        owner.seal({})

    def mutate(records):
        if fault == "missing-end":
            records.remove(
                next(
                    record
                    for record in records
                    if record["family"] == "collections" and record["kind"] == "end"
                )
            )
        elif fault == "edge-kind":
            record = next(
                record
                for record in records
                if record["family"] == "operand:journal-read" and record["kind"] == "end"
            )
            record["edges"][0][2] = "list"
        else:
            notes = [
                record
                for record in records
                if record["family"] == "collections" and record["kind"] == "note"
            ]
            notes[1]["body"] = notes[0]["body"]

    _rewrite_log(root, mutate)
    with pytest.raises(ValueError, match=r"collection|dictionary|original"):
        OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=256 * 1024)


def test_map_stream_copy_preserves_shared_and_independent_producer_nodes(tmp_path):
    from scripts.execution_capacity.original_plain import copy_plain_graph

    with (
        OriginalJournal.create(
            tmp_path / "source", budget=EvidenceBudget(), index_bytes=256 * 1024
        ) as source,
        OriginalJournal.create(
            tmp_path / "target", budget=EvidenceBudget(), index_bytes=256 * 1024
        ) as target,
    ):
        parent = source.begin("operand:journal-read", {})
        children = []
        for slot in ("one", "equal-independent"):
            writer = source.begin_collection(parent, slot)
            writer.append({"value": 1})
            children.append(writer.complete())
        writer = source.begin_dictionary(parent, "history")
        writer.append("first", children[0])
        writer.append("shared", children[0])
        writer.append("independent", children[1])
        rows = writer.complete()
        source.complete(parent, {"map": rows})
        target_parent = target.begin("operand:journal-read", {})
        copied = copy_plain_graph(rows, source=source, target=target, parent=target_parent)
        target.complete(target_parent, {"map": copied})
        actual = target.sequence("operand:journal-read")[0]["map"]
        first, shared, independent = actual["first"], actual["shared"], actual["independent"]
        assert first is shared
        assert first is not independent
        assert first.producer_ordinal != independent.producer_ordinal
        source.close()
        assert list(first) == [{"value": 1}]


def test_dictionary_weak_nodes_are_root_scoped_and_released(tmp_path):
    import gc

    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
    ) as owner:
        parent = owner.begin("operand:journal-read", {})
        child = owner.begin_collection(parent, "rows")
        child.append(1)
        rows = child.complete()
        writer = owner.begin_dictionary(parent, "history")
        writer.append("a", rows)
        writer.append("b", rows)
        mapping = writer.complete()
        owner.complete(parent, {"map": mapping})
        owner.append("cleanup", {"map": mapping})
        left = owner.sequence("operand:journal-read")[0]["map"]
        right = owner.sequence("cleanup")[0]["map"]
        a, b, other = left["a"], left["b"], right["a"]
        assert a is b
        assert a is not other
        assert left is not right
        assert len(owner._cursor_nodes) > 0
        del a, b, other, left, right
        gc.collect()
        assert len(owner._cursor_nodes) == 0
        assert len(owner._root_scopes) == 0


def test_plain_nodes_keep_explicit_aliases_without_merging_same_producer(tmp_path):
    from scripts.execution_capacity.original_collections import PlainCollectionRows

    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_collection(parent, "rows")
        writer.append(1)
        rows = writer.complete()
        first = PlainCollectionRows(owner, rows.producer_ordinal, rows.length, rows.body_descriptor)
        independent = PlainCollectionRows(
            owner, rows.producer_ordinal, rows.length, rows.body_descriptor
        )
        assert first.logical_node != independent.logical_node
        owner.complete(parent, {"first": first, "alias": first, "independent": independent})
        owner.append("cleanup", {"first": first, "alias": first, "independent": independent})
        owner.seal({}, original_roots=True)
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=256 * 1024) as fresh:
        left = fresh.sequence("operand:journal-read")[0]
        right = fresh.sequence("cleanup")[0]
        assert left["first"] is left["alias"]
        assert left["first"] is not left["independent"]
        assert left["first"] is not right["first"]
        assert list(left["first"]) == list(left["independent"]) == [1]


def test_dictionary_sorted_index_preserves_all_original_string_codepoints(tmp_path):
    keys = ["🙂", "\ud800", "é", "a\x00z", "a", "", "\x00"]
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
    ) as owner:
        parent = owner.begin("operand:journal-read", {})
        writer = owner.begin_dictionary(parent, "history")
        for key in keys:
            writer.append(key, "value")
        rows = writer.complete()
        assert list(rows) == keys
        assert list(rows.sorted_keys()) == ["", "\x00", "a", "a\x00z", "é", "\ud800", "🙂"]


def test_finite_map_native_recovery_import_survives_source_closure(tmp_path):
    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.inventory_sql import plain
    from scripts.execution_capacity.observers import RecoveryJournal

    native_root = tmp_path / "native"
    native_root.mkdir(mode=0o700)
    expected = {
        "history": {"b": {"body": [1, True], "receipt": None}, "a": {"body": [], "receipt": 1}}
    }
    identity = canonical_digest(expected)
    with OriginalJournal.create(
        tmp_path / "source", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as source:
        parent = source.begin("operand:journal-read", {})
        writer = source.begin_dictionary(parent, "history")
        for key, value in expected["history"].items():
            writer.append(key, value)
        rows = writer.complete()
        source.complete(parent, {"history": rows})
        with RecoveryJournal(
            native_root, budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as native:
            native.intent("environment_read", identity, {"history": plain(rows)}, body_owner=source)
    target_root = tmp_path / "target"
    with OriginalJournal.create(
        target_root, budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as target:
        with RecoveryJournal(
            native_root, budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as native:
            retained = native.get("environment_read", identity)
            assert list(retained["body"]["history"]) == ["a", "b"]
            value = target.import_native(native.native_view(retained["body"]))
            target.append("cleanup", value)
        assert value["history"]["b"] == {"body": [1, True], "receipt": None}
        target.seal({}, original_roots=True)
    with OriginalJournal.open(
        target_root, budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as fresh:
        rows = fresh.roots()["cleanup"]["history"]
        assert rows["a"] == {"body": [], "receipt": 1}
        assert list(rows) == ["a", "b"]
