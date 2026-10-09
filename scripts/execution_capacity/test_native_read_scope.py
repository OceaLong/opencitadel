"""Native read scopes own one original row and transfer before advancing."""

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal, RecoveryJournal
from scripts.execution_capacity.original_journal import OriginalJournal
from scripts.execution_capacity.test_native_original_bodies import _native_fixture


@pytest.mark.parametrize("readonly", [False, True])
def test_scope_advance_closes_previous_native_cursor_and_missing_get(tmp_path, readonly):
    root, identity, expected = _native_fixture(tmp_path, collection=True)
    cls = ReadOnlyRecoveryJournal if readonly else RecoveryJournal
    with cls(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
        with native.read_scope() as scope:
            first = scope.get("environment_read", identity)
            rows = first["body"]["rows"]
            assert list(rows) == expected["rows"]
            second = scope.get("environment_read", identity)
            with pytest.raises(ValueError, match=r"closed|invalid"):
                list(rows)
            second_rows = second["body"]["rows"]
            assert scope.get("environment_read", "missing") is None
            with pytest.raises(ValueError, match=r"closed|invalid"):
                list(second_rows)
        with pytest.raises(ValueError, match=r"closed|scope"):
            scope.get("environment_read", identity)


def test_scope_iterator_early_close_and_exception_release_native_resources(tmp_path):
    root, identity, expected = _native_fixture(tmp_path, collection=True)
    held = []

    def fail_consumer(scope):
        rows = scope.records("environment_read")
        key, row = next(rows)
        assert key == identity
        held.append(row["body"]["rows"])
        assert list(held[0]) == expected["rows"]
        raise RuntimeError("consumer failed before exhausting rows")

    with RecoveryJournal(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
        with pytest.raises(RuntimeError, match="consumer"), native.read_scope() as scope:
            fail_consumer(scope)
        with pytest.raises(ValueError, match=r"closed|invalid"):
            list(held[0])
        with native.read_scope() as scope:
            assert next(scope.records("environment_read"))[0] == identity
        assert not native.db.in_transaction


def test_scope_has_one_active_slot_and_precharges_before_native_open(tmp_path):
    root, identity, _ = _native_fixture(tmp_path, collection=True)
    budget = EvidenceBudget()
    with RecoveryJournal(root, budget=budget, index_bytes=512 * 1024) as native:
        with native.read_scope() as scope:
            held = scope.get("environment_read", identity)["body"]["rows"]
            with pytest.raises(ValueError, match=r"active|scope"), native.read_scope():
                pass
            assert held[0]["id"] == "original"
        budget.reserve(budget.bytes_limit - budget.bytes, rows=0)
        with pytest.raises(EvidenceQuotaError), native.read_scope():
            pass


def test_scoped_durable_import_survives_early_scope_and_source_close(tmp_path):
    root, identity, expected = _native_fixture(tmp_path, collection=True)
    with OriginalJournal.create(
        tmp_path / "main", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as main:
        with RecoveryJournal(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
            with native.read_scope() as scope:
                body = scope.get("environment_read", identity)["body"]
                copied = main.import_native(scope.native_view(body))
                held = body["rows"]
            with pytest.raises(ValueError, match=r"closed|invalid"):
                list(held)
            assert list(copied["rows"]) == expected["rows"]
        assert list(copied["rows"]) == expected["rows"]
        main.append("cleanup", copied)
        main.seal({"kind": "scoped-import"}, original_roots=True)
    with OriginalJournal.open(
        tmp_path / "main", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as reopened:
        assert list(reopened.sequence("cleanup")[0]["rows"]) == expected["rows"]


def test_internal_native_intent_acknowledge_and_inventory_release_views(tmp_path, monkeypatch):
    from scripts.execution_capacity.native_original_bodies import NativeBodyView

    root, identity, body = _native_fixture(tmp_path, collection=True)
    opened = []
    original = NativeBodyView.__init__

    def observe(view, *args, **kwargs):
        original(view, *args, **kwargs)
        opened.append(view)

    monkeypatch.setattr(NativeBodyView, "__init__", observe)
    with (
        OriginalJournal.create(
            tmp_path / "second-source", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as source,
        RecoveryJournal(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        native.intent("environment_read", identity, body, body_owner=source)
        native.acknowledge("environment_read", identity, {"seen": 1})
        assert any(name.endswith("manifest.json") for name in native.original_files())
        assert opened
        assert all(view.journal.closed for view in opened)


def test_scope_blocks_legacy_get_and_records_from_accumulating_views(tmp_path):
    root, identity, _ = _native_fixture(tmp_path, collection=True)
    with (
        RecoveryJournal(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
        native.read_scope() as scope,
    ):
        row = scope.get("environment_read", identity)
        for read in (
            lambda: native.get("environment_read", identity),
            lambda: list(native.records("environment_read")),
        ):
            with pytest.raises(ValueError, match=r"active|scope"):
                read()
        assert row["body"]["rows"][0]["value"] == 1
