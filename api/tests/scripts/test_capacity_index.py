"""Private disposable index behavior; bounded local SQLite only, no services."""

import contextlib
import json
import os
import sqlite3

import pytest


def test_index_retains_order_and_rejects_cross_batch_duplicate_identity():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=1024 * 1024) as index:
        index.append("progress", b'{"sequence":2}', keys={"event": "unicode-\U0001f642"})
        index.append("progress", b'{"sequence":1}', keys={"event": "other"})
        assert list(index.rows("progress")) == [b'{"sequence":2}', b'{"sequence":1}']
        assert index.find("progress", "event", "other") == b'{"sequence":1}'
        with pytest.raises(ValueError, match="duplicate"):
            index.append("progress", b'{"sequence":3}', keys={"event": "other"})
        with pytest.raises(ValueError, match="invalid"):
            list(index.rows("progress"))


def test_index_exact_numeric_ranks_preserve_python_values_and_ties():
    from scripts.acceptance.capacity_index import CapacityIndex

    values = [1.0, 2**80 + 1, 2**80, 0, 1, 1.5, 2**80 + 2]
    with CapacityIndex(quota_bytes=1024 * 1024) as index:
        for value in values:
            index.number("frames", value)
        # ceil(7 * .5) == 4 and ceil(7 * .95) == 7.
        assert index.rank("frames", 4) == 1.5
        assert index.rank("frames", 7) == 2**80 + 2
        assert type(index.rank("frames", 7)) is int
        assert index.rank("frames", 2) == 1.0
        assert type(index.rank("frames", 2)) is float
        assert type(index.rank("frames", 3)) is int


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), "12"])
def test_index_numeric_input_cannot_coerce_booleans_or_nonfinite(value):
    from scripts.acceptance.capacity_index import CapacityIndex

    with (
        CapacityIndex(quota_bytes=1024 * 1024) as index,
        pytest.raises(ValueError, match="numeric"),
    ):
        index.number("frames", value)


def test_index_page_quota_invalidates_session_and_retains_bounded_failure():
    from scripts.acceptance.capacity_index import CapacityIndex

    index = CapacityIndex(quota_bytes=64 * 1024)
    root = index.root

    def fill():
        for _ in range(20):
            index.append("large", b"x" * 8192)

    try:
        with index:
            with pytest.raises(ValueError, match="quota"):
                fill()
            with pytest.raises(ValueError, match="invalid"):
                index.count("large")
    finally:
        assert (root / "index.sqlite").stat().st_size <= 64 * 1024
        failure = json.loads((root / "failure.json").read_bytes())
        assert failure["state"] == "invalid"
        assert failure["quota_bytes"] == 64 * 1024
        assert len((root / "failure.json").read_bytes()) < 1024
        # This test owns the retained failure; production must preserve it.
        (root / "failure.json").unlink()
        (root / "index.sqlite").unlink()
        root.rmdir()


def test_index_success_closes_and_removes_only_its_owned_scratch():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=1024 * 1024) as index:
        root = index.root
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / "index.sqlite").stat().st_mode & 0o777 == 0o600
        index.append("rows", b"retained")
        assert index.count("rows") == 1
        assert index.query_plans_use_declared_indexes()
    assert not root.exists()


@pytest.mark.parametrize("replace", ["directory", "database"])
def test_index_replacement_is_detected_and_foreign_files_never_deleted(tmp_path, replace):
    from scripts.acceptance.capacity_index import CapacityIndex

    index = CapacityIndex(quota_bytes=1024 * 1024)
    root = index.root
    original = root if replace == "directory" else root / "index.sqlite"
    moved = original.with_name(original.name + "-test-moved")
    original.rename(moved)
    if replace == "directory":
        original.mkdir(mode=0o700)
        foreign = original / "index.sqlite"
    else:
        foreign = original
    foreign.write_bytes(b"foreign")
    os.chmod(foreign, 0o600)
    try:
        with pytest.raises(ValueError, match="identity"):
            index.append("rows", b"unsafe")
        index.close()
        assert foreign.read_bytes() == b"foreign"
        with pytest.raises(ValueError, match="invalid"):
            index.count("rows")
    finally:
        foreign.unlink()
        if replace == "directory":
            original.rmdir()
            for path in moved.iterdir():
                path.unlink()
            moved.rmdir()
        else:
            moved.unlink()
            for path in root.iterdir():
                path.unlink()
            root.rmdir()


def test_index_rejects_external_path_and_oversized_row_before_sqlite(tmp_path):
    from scripts.acceptance.capacity_index import CapacityIndex

    existing = tmp_path / "untrusted.sqlite"
    existing.write_bytes(b"untrusted")
    with pytest.raises(TypeError):
        CapacityIndex(quota_bytes=1024 * 1024, path=existing)
    with CapacityIndex(quota_bytes=1024 * 1024, row_bytes=16) as index:
        with pytest.raises(ValueError, match="row"):
            index.append("rows", b"x" * 17)
        assert index.count("rows") == 0
    assert existing.read_bytes() == b"untrusted"


def test_index_number_ranking_never_materializes_population():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=4 * 1024 * 1024) as index:
        with index.transaction():
            for ordinal in range(10000):
                index.number("frames", ordinal % 100)
        assert index.count_numbers("frames") == 10000
        assert index.rank("frames", 9500) == 94
        assert index.query_plans_use_declared_indexes()
        assert index.metrics["largest_payload_bytes"] <= 2


def test_caught_duplicate_inside_batch_cannot_commit_partial_authority():
    from scripts.acceptance.capacity_index import CapacityIndex

    index = CapacityIndex(quota_bytes=1024 * 1024)

    def catch_inside_transaction():
        with index.transaction():
            index.append("rows", b"first", keys={"id": "same"})
            with contextlib.suppress(ValueError, sqlite3.IntegrityError):
                index.append("rows", b"second", keys={"id": "same"})
            index.count("rows")

    try:
        with index, pytest.raises(ValueError, match="invalid"):
            catch_inside_transaction()
    finally:
        for path in index.root.iterdir():
            path.unlink()
        index.root.rmdir()


def test_read_failure_invalidates_entire_session_even_if_caller_catches_it():
    from scripts.acceptance.capacity_index import CapacityIndex

    index = CapacityIndex(quota_bytes=1024 * 1024)
    try:
        with index:
            index.append("rows", b"row")
            index._connection.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
            with pytest.raises(ValueError, match="storage"):
                index.count("rows")
            index._connection.set_authorizer(index._authorize)
            with pytest.raises(ValueError, match="invalid"):
                index.count("rows")
    finally:
        for path in index.root.iterdir():
            path.unlink()
        index.root.rmdir()


def test_index_identity_order_uses_full_keys_and_declared_index():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=128 * 1024) as index:
        for identity in ("é", "a", "a\x00z", "🙂"):
            index.append("history", identity.encode(), keys={"key": identity})
        assert list(index.identity_rows("history", "key")) == [
            b"a",
            b"a\x00z",
            "é".encode(),
            "🙂".encode(),
        ]
        assert list(index.identity_rows("absent", "key")) == []
        assert index.query_plans_use_declared_indexes()


def test_index_closes_suspended_reader_before_sqlite_connection():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=128 * 1024) as index:
        index.append("history", b"first", keys={"key": "a"})
        index.append("history", b"second", keys={"key": "b"})
        reader = index.identity_rows("history", "key")
        assert next(reader) == b"first"
        assert len(index._readers) == 1
    assert not index._readers
    reader.close()


def test_index_group_candidates_keep_all_occurrences_and_exact_group_identity():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=128 * 1024) as index:
        index.group("lease", "full-key", b"first")
        index.group("lease", "full-key-other", b"foreign")
        index.group("lease", "full-key", b"second")
        assert list(index.group_rows("lease", "full-key")) == [b"first", b"second"]
        assert list(index.group_rows("lease", "missing")) == []
        assert index.query_plans_use_declared_indexes()


def test_index_last_group_lookup_keeps_all_ordered_duplicates():
    from scripts.acceptance.capacity_index import CapacityIndex

    with CapacityIndex(quota_bytes=128 * 1024) as index:
        for raw in (b"first", b"second", b"third"):
            index.group("derived", "key", raw)
        assert index.group_last("derived", "key") == b"third"
        assert index.group_last("derived", "missing") is None
        assert list(index.group_rows("derived", "key")) == [b"first", b"second", b"third"]
        assert index.query_plans_use_declared_indexes()
