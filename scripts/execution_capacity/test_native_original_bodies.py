"""Private-file-only native recovery body ownership and exact JSON semantics."""

from uuid import UUID

import pytest
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.inventory_sql import plain
from scripts.execution_capacity.observers import RecoveryJournal
from scripts.execution_capacity.original_journal import OriginalJournal


def test_environment_read_body_outlives_acquisition_owner_and_reopens_all_read_interfaces(tmp_path):
    source = OriginalJournal.create(
        tmp_path / "source", budget=EvidenceBudget(), index_bytes=512 * 1024
    )
    parent = source.begin("operand:query-rows", {"name": "leases"})
    writer = source.begin_collection(parent, "rows")
    expected = [{"id": UUID(int=7), "value": True}, {"id": UUID(int=8), "value": 1}]
    for row in expected:
        writer.append(row)
    rows = writer.complete()
    source.complete(parent, {"rows": rows})
    body = {"leases": plain(rows), "query_observations": [{"rows": plain(rows)}], "error": None}
    oracle = {
        "leases": plain(expected),
        "query_observations": [{"rows": plain(expected)}],
        "error": None,
    }
    identity = canonical_digest(oracle)
    target = tmp_path / "native"
    target.mkdir(mode=0o700)
    try:
        with RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
            native.intent("environment_read", identity, body, body_owner=source)
            assert native.db.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert native.db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        source.close()
    with RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as fresh:
        variants = [
            fresh.get("environment_read", identity),
            fresh.bounded_get("environment_read", identity, fresh.budget),
            dict(fresh.records("environment_read"))[identity],
            dict(fresh.bounded_records("environment_read", fresh.budget))[identity],
        ]
        for retained in variants:
            assert list(retained["body"]["leases"]) == oracle["leases"]
            assert list(retained["body"]["query_observations"][0]["rows"]) == oracle["leases"]
            assert retained["receipt"] is None


def test_legacy_recovery_body_fields_cannot_be_misread_as_a_native_reference(tmp_path):
    body = {
        "version": 2,
        "namespace": "native",
        "reference": "not-a-reference",
        "schema": "native-body-v2",
    }
    (tmp_path / "native").mkdir(mode=0o700)
    with RecoveryJournal(tmp_path / "native") as native:
        native.intent("environment_read", "legacy", body)
        assert native.get("environment_read", "legacy") == {"body": body, "receipt": None}


@pytest.mark.parametrize("mutation", ["identity", "body"])
def test_native_environment_read_rejects_nonmatching_expanded_identity(tmp_path, mutation):
    with OriginalJournal.create(
        tmp_path / "source", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as source:
        parent = source.begin("operand:query-rows", {})
        writer = source.begin_collection(parent, "rows")
        writer.append({"row": 1})
        rows = writer.complete()
        source.complete(parent, {"rows": rows})
        identity = canonical_digest({"rows": [{"row": 1}]})
        body = {"rows": plain(rows)}
        if mutation == "identity":
            identity = "0" * 64
        else:
            body["extra"] = True
        (tmp_path / "native").mkdir(mode=0o700)
        with RecoveryJournal(
            tmp_path / "native", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as native:
            with pytest.raises(ValueError, match="identity"):
                native.intent("environment_read", identity, body, body_owner=source)
            assert native.get("environment_read", identity) is None


def _native_fixture(tmp_path, *, collection=False):
    target = tmp_path / "native"
    target.mkdir(mode=0o700)
    body = {"rows": [{"id": "original", "value": 1}]}
    identity = canonical_digest(body)
    with (
        OriginalJournal.create(
            tmp_path / "source", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as source,
        RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        payload = body
        if collection:
            parent = source.begin("operand:query-rows", {"name": "native"})
            writer = source.begin_collection(parent, "rows")
            for row in body["rows"]:
                writer.append(row)
            rows = writer.complete()
            source.complete(parent, {"rows": rows})
            payload = {"rows": plain(rows)}
        native.intent("environment_read", identity, payload, body_owner=source)
    return target, identity, body


def test_missing_native_format_row_cannot_downgrade_to_legacy_null(tmp_path):
    target, identity, _ = _native_fixture(tmp_path)
    with RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
        with native.db:
            native.db.execute(
                "DELETE FROM original_bodies WHERE kind=? AND identity=?",
                ("environment_read", identity),
            )
        with pytest.raises(ValueError, match="reference"):
            native.get("environment_read", identity)


def test_readonly_native_journal_reopens_same_longlived_originals(tmp_path):
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    target, identity, body = _native_fixture(tmp_path)
    with ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
        assert native.get("environment_read", identity) == {"body": body, "receipt": None}
        assert (
            dict(native.bounded_records("environment_read", native.budget))[identity]["body"]
            == body
        )


def test_native_body_and_receipt_remain_immutable_without_duplicate_files(tmp_path):
    target, identity, body = _native_fixture(tmp_path)
    with (
        OriginalJournal.create(
            tmp_path / "second-source", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as source,
        RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        before = sorted(path.name for path in target.iterdir())
        native.intent("environment_read", identity, body, body_owner=source)
        assert sorted(path.name for path in target.iterdir()) == before
        native.acknowledge("environment_read", identity, {"receipt": 1})
        native.acknowledge("environment_read", identity, {"receipt": 1})
        with pytest.raises(ValueError, match="immutable recovery receipt"):
            native.acknowledge("environment_read", identity, {"receipt": True})
        assert native.get("environment_read", identity)["receipt"] == {"receipt": 1}


def test_native_sql_commit_failure_retains_closed_raw_prefix_without_authority(tmp_path):
    import sqlite3

    target = tmp_path / "native"
    target.mkdir(mode=0o700)
    body = {"rows": []}
    identity = canonical_digest(body)
    with (
        OriginalJournal.create(
            tmp_path / "source", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as source,
        RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        native.db.execute(
            "CREATE TRIGGER fixture_failure BEFORE INSERT ON original_bodies BEGIN SELECT RAISE(ABORT, 'fixture reference commit'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="fixture reference commit"):
            native.intent("environment_read", identity, body, body_owner=source)
        assert native.get("environment_read", identity) is None
        prefixes = list(target.glob("original-*"))
        assert len(prefixes) == 1
        assert (prefixes[0] / "manifest.json").exists()
        assert source.budget.bytes > 0


def test_native_file_inventory_drives_actual_copy_and_fresh_reopen(tmp_path):
    from hashlib import sha256

    from scripts.acceptance.capacity_io import copy_artifacts
    from scripts.acceptance.capacity_models import Artifact
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    target, identity, body = _native_fixture(tmp_path)
    with ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native:
        names = ["recovery.sqlite3", *native.original_files()]
        assert any(name.endswith("manifest.json") for name in names)
        artifacts = [
            Artifact(
                path=name,
                role="measurements",
                schema_version=3,
                sha256=sha256((target / name).read_bytes()).hexdigest(),
                size_bytes=(target / name).stat().st_size,
            )
            for name in names
        ]
        copy_artifacts(artifacts, target, tmp_path / "copied")
    with ReadOnlyRecoveryJournal(
        tmp_path / "copied", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as fresh:
        assert fresh.get("environment_read", identity) == {"body": body, "receipt": None}
        assert list(fresh.original_files()) == names[1:]


def test_native_file_inventory_rejects_unreferenced_retained_prefix(tmp_path):
    target, _, _ = _native_fixture(tmp_path)
    (target / ("original-" + "a" * 32)).mkdir(mode=0o700)
    with (
        RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
        pytest.raises(ValueError, match=r"native original.*closure"),
    ):
        list(native.original_files())


def test_native_body_fsync_failure_precedes_sql_reference_and_retains_prefix(tmp_path, monkeypatch):
    import os

    target = tmp_path / "native"
    target.mkdir(mode=0o700)
    body = {"rows": []}
    identity = canonical_digest(body)
    with (
        OriginalJournal.create(
            tmp_path / "source", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as source,
        RecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        real = os.fsync
        target_stat = target.stat()
        calls = []

        def fail_parent(fd):
            actual = os.fstat(fd)
            if (actual.st_dev, actual.st_ino) == (target_stat.st_dev, target_stat.st_ino):
                calls.append("native-parent")
                raise OSError("fixture native parent fsync")
            real(fd)

        monkeypatch.setattr(os, "fsync", fail_parent)
        with pytest.raises(OSError, match="fixture native parent fsync"):
            native.intent("environment_read", identity, body, body_owner=source)
        assert calls == ["native-parent"]
        assert native.get("environment_read", identity) is None
        assert list(target.glob("original-*"))


def test_native_reference_byte_preflight_precedes_text_transfer(tmp_path):
    from scripts.execution_capacity.evidence_bounds import EvidenceQuotaError

    target, identity, _ = _native_fixture(tmp_path)
    with RecoveryJournal(
        target, budget=EvidenceBudget(row_limit=4096), index_bytes=512 * 1024
    ) as native:
        with native.db:
            native.db.execute("UPDATE original_bodies SET reference=?", ("x" * 10000,))
        statements = []
        native.db.set_trace_callback(statements.append)
        with pytest.raises(EvidenceQuotaError):
            native.get("environment_read", identity)
        assert not any(statement.startswith("SELECT version,reference") for statement in statements)


def test_live_native_import_becomes_main_owned_and_survives_source_removal_and_copy(tmp_path):
    import shutil
    from hashlib import sha256

    from scripts.acceptance.capacity_io import copy_artifacts
    from scripts.acceptance.capacity_models import Artifact
    from scripts.execution_capacity.guest_seal_entry import artifact_relative, original_artifacts
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    target, identity, body = _native_fixture(tmp_path, collection=True)
    root = tmp_path / "main" / "c2c-originals"
    root.parent.mkdir(mode=0o700)
    with (
        OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as main,
        ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        value = native.get("environment_read", identity)["body"]
        imported = main.import_native(native.native_view(value))
        assert list(imported["rows"]) == body["rows"]
        main.append("cleanup", {"body": imported})
        main.seal({}, original_roots=True)
    shutil.rmtree(target)
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as fresh:
        assert list(fresh.sequence("cleanup")[0]["body"]["rows"]) == body["rows"]
        names = ["c2c-originals/manifest.json"] + [
            artifact_relative(name) for name, _ in original_artifacts(fresh.manifest)
        ]
        assert any("native-" in name for name in names)
        artifacts = [
            Artifact(
                path=name,
                role="measurements",
                schema_version=3,
                size_bytes=(root.parent / name).stat().st_size,
                sha256=sha256((root.parent / name).read_bytes()).hexdigest(),
            )
            for name in names
        ]
        copy_artifacts(artifacts, root.parent, tmp_path / "copied-main")
    with OriginalJournal.open(
        tmp_path / "copied-main" / "c2c-originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as copied:
        assert list(copied.sequence("cleanup")[0]["body"]["rows"]) == body["rows"]


@pytest.mark.parametrize("fault", ["foreign", "closed", "incomplete"])
def test_native_import_requires_actual_live_complete_view(tmp_path, fault):
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    target, identity, _ = _native_fixture(tmp_path, collection=True)
    with (
        OriginalJournal.create(
            tmp_path / "main", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as main,
        ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        view = native.native_view(native.get("environment_read", identity)["body"])
        if fault == "foreign":
            view = object()
        elif fault == "closed":
            view.close()
        else:
            view.journal.sealed = False
        with pytest.raises(ValueError, match=r"live|closed|complete"):
            main.import_native(view)
        assert main.index.count("begin:native-imports") == 0


def test_import_namespace_mutation_cannot_be_sealed(tmp_path):
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    target, identity, _ = _native_fixture(tmp_path, collection=True)
    with (
        OriginalJournal.create(
            tmp_path / "main", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as main,
        ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        view = native.native_view(native.get("environment_read", identity)["body"])
        main.import_native(view)
        entry = main.native_bodies[0]
        path = main.root / "native-000000" / entry["reference"]["directory"] / "000000.bin"
        path.write_bytes(path.read_bytes() + b"changed")
        with pytest.raises(ValueError, match=r"segment|bytes"):
            main.seal({})
        assert not (main.root / "manifest.json").exists()


def test_native_import_copy_precharges_members_and_bytes_and_retains_failure_prefix(
    tmp_path, monkeypatch
):
    from scripts.execution_capacity import original_imports
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    class MeasuredBudget(EvidenceBudget):
        def __init__(self):
            super().__init__()
            self.charges = []

        def reserve(self, size, rows=1, *, largest=None):
            super().reserve(size, rows, largest=largest)
            self.charges.append((size, rows))

    target, identity, _ = _native_fixture(tmp_path, collection=True)
    budget = MeasuredBudget()
    copy = original_imports.copy_artifacts

    def fail_after_copy(artifacts, source, destination):
        assert (sum(item.size_bytes for item in artifacts), 0) in budget.charges
        assert (len(artifacts) * 256, len(artifacts)) in budget.charges
        copy(artifacts, source, destination)
        raise OSError("private copy completion fault")

    monkeypatch.setattr(original_imports, "copy_artifacts", fail_after_copy)
    with (
        OriginalJournal.create(tmp_path / "main", budget=budget, index_bytes=512 * 1024) as main,
        ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        view = native.native_view(native.get("environment_read", identity)["body"])
        with pytest.raises(OSError, match="completion fault"):
            main.import_native(view)
        assert main.index.count("begin:native-imports") == 1
        assert main.index.count("end:native-imports") == 0
        assert (
            main.root / "native-000000" / view.reference["directory"] / "manifest.json"
        ).is_file()
        assert main.native_bodies == []
        with pytest.raises(ValueError, match=r"closed|invalid|failed"):
            main.seal({})
        assert not (main.root / "manifest.json").exists()


def test_native_import_extra_namespace_member_rejected_before_seal(tmp_path):
    from scripts.execution_capacity.observers import ReadOnlyRecoveryJournal

    target, identity, _ = _native_fixture(tmp_path, collection=True)
    with (
        OriginalJournal.create(
            tmp_path / "main", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as main,
        ReadOnlyRecoveryJournal(target, budget=EvidenceBudget(), index_bytes=512 * 1024) as native,
    ):
        main.import_native(native.native_view(native.get("environment_read", identity)["body"]))
        (main.root / "native-000000" / "extra").write_bytes(b"unreferenced")
        with pytest.raises(ValueError, match="membership"):
            main.seal({})
        assert not (main.root / "manifest.json").exists()
