"""Private-file-only acquisition lifecycle; no provider or database effects."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError


def journal(tmp_path, **limits):
    from scripts.execution_capacity.original_journal import OriginalJournal

    return OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(**limits), index_bytes=64 * 1024
    )


def test_failed_owner_keeps_durable_begin_and_small_prefix_without_iterating_rows(tmp_path):
    import json
    from types import SimpleNamespace

    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.original_journal import OriginalJournal

    class ForbiddenRows:
        def __len__(self):
            raise AssertionError("failed prefix must not reconstruct raw originals")

    owner = EvidenceOwner(original_root=tmp_path / "c2c-originals", index_bytes=64 * 1024)
    try:
        owner.journal.begin("sql", {"statement": "select original", "start_ns": 1})
        prefix = (owner.journal.root / "occurrences.jsonl").read_bytes()
        owner.journal.valid = False
        resources = SimpleNamespace(evidence_transport=SimpleNamespace(originals=ForbiddenRows()))
        owner.retain_failure(tmp_path, ValueError("private failure"), resources=resources)
        retained = json.loads((tmp_path / "c2c-failed-originals.json").read_bytes())
        assert retained["schema"] == "opencitadel.acquisition-failure.v2"
        assert retained["complete"] is False
        assert retained["original_prefix"]["directory"] == "c2c-originals"
        assert retained["original_prefix"]["records"] == 1
        assert len((tmp_path / "c2c-failed-originals.json").read_bytes()) < 4096
        assert (owner.journal.root / "occurrences.jsonl").read_bytes() == prefix
        assert not (owner.journal.root / "manifest.json").exists()
    finally:
        owner.journal.close()
    with pytest.raises((ValueError, OSError)):
        OriginalJournal.open(
            tmp_path / "c2c-originals", budget=EvidenceBudget(), index_bytes=64 * 1024
        )


def test_cleanup_prefix_is_durable_before_small_wire_and_only_complete_root_seals(tmp_path):
    import json

    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.original_shards import OriginalView

    owner = EvidenceOwner(original_root=tmp_path / "c2c-originals", index_bytes=64 * 1024)
    try:
        owner.begin_cleanup()
        assert owner.journal.index.count("begin:cleanup") == 1
        token = owner.journal.begin("operand:query-rows", {})
        producer = owner.journal.begin_collection(token, "rows")
        producer.append({"row": 1})
        rows = producer.complete()
        owner.journal.complete(token, {"rows": rows})
        evidence = {"quiescence": {"rows": rows}, "errors": [], "observer_closed_ns": 2}
        owner.retain_cleanup_prefix(tmp_path, evidence)
        wire = json.loads((tmp_path / "quiescence-private.json").read_bytes())
        assert wire["schema"] == "opencitadel.acquisition-prefix.v2"
        assert "quiescence" not in wire
        assert wire["complete"] is False
        assert owner.journal.index.count("note:cleanup:0") == 1
        assert owner.journal.index.count("end:cleanup") == 0
        roots = {
            "cleanup": evidence,
            "operands": owner.originals,
            "sql": owner.sql_reads,
            "objects": owner.journal.sequence("objects"),
            "transports": owner.journal.sequence("transports"),
        }
        owner.finish_originals(roots, {})
    finally:
        owner.journal.close()
    with OriginalView.open(
        tmp_path / "c2c-originals", budget=EvidenceBudget(), index_bytes=64 * 1024
    ) as fresh:
        assert list(fresh.materialize()["cleanup"]["quiescence"]["rows"]) == [{"row": 1}]


def test_actual_occurrences_keep_order_types_and_independent_snapshots(tmp_path):
    with journal(tmp_path) as owner:
        raw = {
            "uuid": UUID(int=1),
            "bytes": b"\x00\xff",
            "decimal": Decimal("1.00"),
            "when": datetime(2026, 9, 7, tzinfo=UTC),
            "rows": [False, 1, "1"],
        }
        first = owner.begin("sql", {"uow": 0, "start_ns": 1})
        second = owner.begin("sql", {"uow": 1, "start_ns": 2})
        owner.complete(second, raw)
        owner.complete(first, raw)
        raw["rows"].append("later caller mutation")
        assert len(owner.sequence("sql")) == 2
        assert owner.sequence("sql")[0] == owner.sequence("sql")[1]
        assert owner.sequence("sql")[0]["rows"] == [False, 1, "1"]
        assert owner.sequence("sql")[0]["uuid"] == UUID(int=1)
        assert owner.sequence("sql")[0]["decimal"].as_tuple() == Decimal("1.00").as_tuple()
        assert owner.sequence("sql")[0]["bytes"] == b"\x00\xff"
        owner.seal({"actual-owner": "fixture"})


def test_reopen_rebuilds_lookup_from_original_bytes(tmp_path):
    from scripts.execution_capacity.original_journal import OriginalJournal

    with journal(tmp_path) as owner:
        owner.append("operand:event-source", {"event": UUID(int=9), "ordinal": 0})
        owner.seal({"actual-owner": "fixture"})
        root = owner.root
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024) as reopened:
        assert reopened.binding == {"actual-owner": "fixture"}
        assert reopened.sequence("operand:event-source")[0]["event"] == UUID(int=9)


@pytest.mark.parametrize("fault", [None, "bad-line", "cancel"])
def test_rebuild_line_workspace_releases_after_index_or_failure(tmp_path, monkeypatch, fault):
    import asyncio
    import json
    from hashlib import sha256

    from scripts.execution_capacity.attempt import encode
    from scripts.execution_capacity.original_journal import OriginalJournal
    from scripts.execution_capacity.original_segments import OriginalSegments

    with journal(tmp_path) as original:
        original.append("operand:event-source", {"event": UUID(int=9), "ordinal": 0})
        original.append("operand:event-source", {"event": UUID(int=10), "ordinal": 1})
        original.seal({"actual-owner": "fixture"})
        root = original.root
    log_path = root / "occurrences.jsonl"
    if fault == "bad-line":
        raw = bytearray(log_path.read_bytes())
        raw[raw.index(b"\n") + 1] = ord("!")
        log_path.write_bytes(raw)
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["log_chunks"][0]["sha256"] = sha256(raw).hexdigest()
        manifest["log_sha256"] = sha256(raw).hexdigest()
        manifest_path.write_bytes(encode(manifest))
    before = sha256(log_path.read_bytes()).hexdigest()
    budget = EvidenceBudget()
    seen, closed = [], []
    actual_lines = OriginalSegments.lines

    def lines(self, limit):
        try:
            yield from actual_lines(self, limit)
        finally:
            closed.append(True)

    monkeypatch.setattr(OriginalSegments, "lines", lines)
    if fault == "cancel":
        actual_line = OriginalJournal._rebuild_line

        def cancelled(self, line, offset, parsed_hash):
            seen.append(self)
            if self.records == 1:
                raise asyncio.CancelledError
            return actual_line(self, line, offset, parsed_hash)

        monkeypatch.setattr(OriginalJournal, "_rebuild_line", cancelled)
        with pytest.raises(asyncio.CancelledError):
            OriginalJournal.open(root, budget=budget, index_bytes=64 * 1024)
    elif fault == "bad-line":
        actual_line = OriginalJournal._rebuild_line

        def capture(self, line, offset, parsed_hash):
            seen.append(self)
            return actual_line(self, line, offset, parsed_hash)

        monkeypatch.setattr(OriginalJournal, "_rebuild_line", capture)
        with pytest.raises(ValueError, match=r".+"):
            OriginalJournal.open(root, budget=budget, index_bytes=64 * 1024)
    else:
        with OriginalJournal.open(root, budget=budget, index_bytes=64 * 1024) as reopened:
            assert [row["event"] for row in reopened.sequence("operand:event-source")] == [
                UUID(int=9),
                UUID(int=10),
            ]
    assert closed == [True]
    assert budget.workspace_bytes == 0
    assert budget.workspace_work_bytes > 0
    assert budget.workspace_peak_bytes > 0
    assert budget.bytes > 0
    assert sha256(log_path.read_bytes()).hexdigest() == before
    if seen:
        assert seen[-1].closed
        assert seen[-1].log_segments.fd is None


@pytest.mark.parametrize("cancel", [False, True])
def test_large_parsed_rebuild_line_traceback_promotes_scope_and_closes_owner(
    tmp_path, monkeypatch, cancel
):
    import asyncio
    from hashlib import sha256

    from scripts.acceptance.capacity_io import strict_json
    from scripts.execution_capacity.attempt import encode
    from scripts.execution_capacity.original_journal import OriginalJournal

    with journal(tmp_path) as original:
        original.append("sql", {"value": 1})
        original.seal({"actual-owner": "fixture"})
        root = original.root
    log_path = root / "occurrences.jsonl"
    parsed = strict_json(log_path.read_bytes().splitlines()[0])
    parsed["unexpected_large_field"] = list(range(1000))
    raw = encode(parsed) + b"\n"
    log_path.write_bytes(raw)
    manifest_path = root / "manifest.json"
    manifest = strict_json(manifest_path.read_bytes())
    manifest["log_bytes"] = len(raw)
    manifest["log_sha256"] = sha256(raw).hexdigest()
    manifest["log_chunks"][0]["bytes"] = len(raw)
    manifest["log_chunks"][0]["sha256"] = sha256(raw).hexdigest()
    manifest_path.write_bytes(encode(manifest))
    before = sha256(log_path.read_bytes()).hexdigest()
    owners = []
    actual_line = OriginalJournal._rebuild_line

    def late_line(self, line, offset, parsed_hash):
        owners.append(self)
        if cancel:
            decoded = strict_json(line)
            assert len(decoded["unexpected_large_field"]) == 1000
            raise asyncio.CancelledError()
        return actual_line(self, line, offset, parsed_hash)

    monkeypatch.setattr(OriginalJournal, "_rebuild_line", late_line)
    parent = EvidenceBudget(bytes_limit=64 * 1024 * 1024, rows_limit=50_000)
    budget = parent.child()
    expected = asyncio.CancelledError if cancel else ValueError
    with pytest.raises(expected) as error:
        OriginalJournal.open(root, budget=budget, index_bytes=64 * 1024)
    retained = None
    traceback = error.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name in ("_rebuild_line", "late_line"):
            retained = traceback.tb_frame.f_locals.get(
                "row", traceback.tb_frame.f_locals.get("decoded")
            )
        traceback = traceback.tb_next
    assert type(retained) is dict
    assert len(retained["unexpected_large_field"]) == 1000
    assert budget.workspace_bytes == parent.workspace_bytes == 0
    assert budget.bytes >= len(raw) * 64
    assert parent.bytes >= len(raw) * 64
    assert budget.workspace_work_bytes >= len(raw) * 64
    assert sha256(log_path.read_bytes()).hexdigest() == before
    assert owners[0].closed
    assert owners[0].body_segments.fd is None
    assert owners[0].log_segments.fd is None
    assert owners[0].index._closed


def test_index_quota_tracks_two_live_owners_and_suspended_reader(tmp_path):
    from scripts.execution_capacity.original_journal import OriginalJournal

    budget = EvidenceBudget(bytes_limit=4 * 1024 * 1024, rows_limit=65_536)
    base = OriginalJournal.create(tmp_path / "base-index", budget=budget, index_bytes=64 * 1024)
    child = OriginalJournal.create(tmp_path / "child-index", budget=budget, index_bytes=64 * 1024)
    try:
        assert budget.workspace_bytes == 128 * 1024
        assert budget.workspace_peak_bytes == 128 * 1024
        token = child.begin("sql", {"query": 1})
        child.note(token, {"body": "first"})
        child.note(token, {"body": "second"})
        reader = child.notes("sql", 0)
        assert next(reader) == {"body": "first"}
        assert child.index._readers
        child.close()
        assert not child.index._readers
        assert budget.workspace_bytes == 64 * 1024
        reader.close()
    finally:
        child.close()
        base.close()
    assert budget.workspace_bytes == 0
    assert budget.workspace_work_bytes == 128 * 1024
    assert budget.bytes > 0


def test_index_quota_waits_for_worker_read_before_owner_close(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from scripts.execution_capacity.original_journal import OriginalJournal

    budget = EvidenceBudget(bytes_limit=4 * 1024 * 1024, rows_limit=65_536)
    owner = OriginalJournal.create(tmp_path / "worker-index", budget=budget, index_bytes=64 * 1024)
    owner.append("sql", {"body": "original"})
    entered, resume, closing = threading.Event(), threading.Event(), threading.Event()
    actual_record = owner._record_value

    def paused(row):
        entered.set()
        assert resume.wait(5)
        return actual_record(row)

    monkeypatch.setattr(owner, "_record_value", paused)

    def close_owner():
        closing.set()
        owner.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(lambda: owner.sequence("sql")[0])
            assert entered.wait(5)
            close = pool.submit(close_owner)
            assert closing.wait(5)
            assert budget.workspace_bytes == 64 * 1024
            assert not close.done()
            resume.set()
            assert read.result() == {"body": "original"}
            close.result()
        assert budget.workspace_bytes == 0
    finally:
        resume.set()
        owner.close()


def test_index_constructor_failure_releases_only_unopened_owner_quota(tmp_path, monkeypatch):
    from scripts.execution_capacity import original_journal

    budget = EvidenceBudget(bytes_limit=4 * 1024 * 1024, rows_limit=65_536)

    def fail_index(**_):
        raise ValueError("index creation failed")

    monkeypatch.setattr(original_journal, "CapacityIndex", fail_index)
    with pytest.raises(ValueError, match="index creation failed"):
        original_journal.OriginalJournal.create(
            tmp_path / "failed-index", budget=budget, index_bytes=64 * 1024
        )
    assert (budget.bytes, budget.workspace_bytes, budget.workspace_work_bytes) == (0, 0, 64 * 1024)


def test_cancelled_await_keeps_worker_index_quota_until_close_joins(tmp_path, monkeypatch):
    import asyncio
    import threading

    from scripts.execution_capacity.original_journal import OriginalJournal

    budget = EvidenceBudget(bytes_limit=4 * 1024 * 1024, rows_limit=65_536)
    owner = OriginalJournal.create(tmp_path / "cancel-index", budget=budget, index_bytes=64 * 1024)
    owner.append("sql", {"body": "original"})
    entered, resume = threading.Event(), threading.Event()
    actual_record = owner._record_value

    def paused(row):
        entered.set()
        assert resume.wait(5)
        return actual_record(row)

    monkeypatch.setattr(owner, "_record_value", paused)

    async def run():
        read = asyncio.create_task(asyncio.to_thread(lambda: owner.sequence("sql")[0]))
        assert await asyncio.to_thread(entered.wait, 5)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        assert budget.workspace_bytes == 64 * 1024
        closing = asyncio.create_task(asyncio.to_thread(owner.close))
        await asyncio.sleep(0)
        assert budget.workspace_bytes == 64 * 1024
        resume.set()
        await closing

    try:
        asyncio.run(run())
        assert budget.workspace_bytes == 0
    finally:
        resume.set()
        owner.close()


def test_real_decoded_graphs_can_overlap_after_notes_and_worker_reads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from scripts.execution_capacity.original_journal import OriginalJournal

    with journal(tmp_path) as original:
        token = original.begin("sql", {"query": 1})
        original.note(token, {"note": [1]})
        original.complete(token, {"row": [2]})
        original.seal({"actual-owner": "fixture"})
        root = original.root
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024) as owner:
        first = owner.sequence("sql")[0]
        notes = owner.notes("sql", 0)
        note = next(notes)
        assert note == {"note": [1]}
        # Suspended notes holds the owner's RLock, so a worker reader cannot
        # finish until its generator closes; the yielded graph remains live.
        started = Event()

        def worker_read():
            started.set()
            return owner.sequence("sql")[0]

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker_read)
            assert started.wait(5)
            assert not future.done()
            notes.close()
            worker = future.result()
        second = owner.sequence("sql")[0]
        held = [first, note, worker, second]
        assert len({id(value) for value in held}) == 4
        assert [value["row"] for value in (first, worker, second)] == [[2]] * 3


@pytest.mark.parametrize("fault", [None, "reject", "cancel"])
def test_rebuild_borrows_only_closed_collection_parent_metadata(tmp_path, monkeypatch, fault):
    import asyncio
    from hashlib import sha256

    from scripts.execution_capacity import original_journal

    with journal(tmp_path) as original:
        token = original.begin("operand:query-rows", {})
        writer = original.begin_collection(token, "rows")
        writer.append({"row": 1})
        rows = writer.complete()
        original.complete(token, {"rows": rows})
        original.seal({"actual-owner": "fixture"})
        root = original.root
    before = sha256((root / "occurrences.jsonl").read_bytes()).hexdigest()
    decoded, visited, owners, borrowed_sizes, late_bytes = [], [], [], [], []
    actual_decode = original_journal._decode
    actual_visit = original_journal.OriginalJournal._validate_collection_parent

    def decode(raw, budget, **kwargs):
        value = actual_decode(raw, budget, **kwargs)
        if (
            kwargs.get("_borrowed_prepaid")
            and type(value) is dict
            and set(value) == {"family", "ordinal", "slot"}
        ):
            decoded.append(id(value))
            borrowed_sizes.append(len(raw) * 64)
        return value

    def visit(self, value):
        owners.append(self)
        assert type(value) is dict
        assert set(value) == {"family", "ordinal", "slot"}
        before_cursors = len(self._cursor_nodes)
        visited.append(id(value))
        result = actual_visit(self, value)
        assert result is None
        assert len(self._cursor_nodes) == before_cursors
        late_bytes.append(self.budget.bytes)
        if fault == "reject":
            raise ValueError("collection parent rejected after index write")
        if fault == "cancel":
            raise asyncio.CancelledError

    monkeypatch.setattr(original_journal, "_decode", decode)
    monkeypatch.setattr(original_journal.OriginalJournal, "_validate_collection_parent", visit)
    budget = EvidenceBudget()
    error = None
    if fault == "reject":
        with pytest.raises(ValueError, match="collection parent rejected") as error:
            original_journal.OriginalJournal.open(root, budget=budget, index_bytes=64 * 1024)
    elif fault == "cancel":
        with pytest.raises(asyncio.CancelledError) as error:
            original_journal.OriginalJournal.open(root, budget=budget, index_bytes=64 * 1024)
    else:
        with original_journal.OriginalJournal.open(
            root, budget=budget, index_bytes=64 * 1024
        ) as reopened:
            assert reopened.sequence("operand:query-rows")[0]["rows"]
    assert decoded
    assert decoded == visited
    assert budget.workspace_bytes == 0
    assert budget.workspace_work_bytes > 0
    assert budget.bytes > 0
    assert sha256((root / "occurrences.jsonl").read_bytes()).hexdigest() == before
    if fault:
        assert budget.bytes >= late_bytes[-1] + borrowed_sizes[-1]
        retained = None
        traceback = error.value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_code.co_name == "visit":
                retained = traceback.tb_frame.f_locals["value"]
            traceback = traceback.tb_next
        assert type(retained) is dict
        assert id(retained) == visited[-1]
        assert owners[-1].closed
        assert owners[-1].log_segments.fd is None


@pytest.mark.parametrize("failure", ["pending", "foreign", "duplicate", "closed"])
def test_closure_requires_unique_owned_completion(tmp_path, failure):
    with journal(tmp_path) as owner:
        token = owner.begin("sql", {"uow": 0})
        if failure == "pending":
            with pytest.raises(ValueError, match="pending"):
                owner.seal({})
        elif failure == "foreign":
            with pytest.raises(ValueError, match="owned"):
                owner.complete(object(), {})
        elif failure == "duplicate":
            owner.complete(token, {})
            with pytest.raises(ValueError, match="completed"):
                owner.complete(token, {})
        else:
            owner.close()
            with pytest.raises(ValueError, match="closed"):
                owner.complete(token, {})
        assert not (owner.root / "manifest.json").exists()


@pytest.mark.parametrize("mutation", ["missing", "modified", "foreign"])
def test_reopen_rejects_raw_closure_mutations(tmp_path, mutation):
    import os

    from scripts.execution_capacity.original_journal import OriginalJournal

    with journal(tmp_path) as owner:
        owner.append("sql", {"uow": 1})
        owner.seal({})
        root = owner.root
    chunk = root / "000000.bin"
    if mutation == "missing":
        chunk.unlink()
    elif mutation == "modified":
        with chunk.open("r+b") as stream:
            stream.write(b"!")
    else:
        os.chmod(chunk, 0o644)
    with pytest.raises((ValueError, FileNotFoundError)):
        OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024)


def test_short_write_is_completed_but_fsync_failure_never_seals(tmp_path, monkeypatch):
    import os

    real_write = os.write
    with journal(tmp_path) as owner:
        monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:7]))
        token = owner.begin("sql", {"uow": 1})
        owner.complete(token, {"data": b"typed original"})
        assert owner.sequence("sql")[0]["data"] == b"typed original"

        def fail(fd):
            raise OSError("injected durable failure")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(OSError, match="injected"):
            owner.append("sql", {"uow": 2})
        with pytest.raises(ValueError, match="invalid"):
            owner.seal({})
        assert not (owner.root / "manifest.json").exists()


def test_quota_before_write_retains_incomplete_prefix(tmp_path):
    with journal(tmp_path, bytes_limit=100_000, rows_limit=1000, row_limit=4096) as owner:
        owner.begin("sql", {"uow": 0})
        with pytest.raises(EvidenceQuotaError):
            owner.append("sql", {"too-large": "x" * 100_000})
        assert (owner.root / "occurrences.jsonl").stat().st_size > 0
        assert not (owner.root / "manifest.json").exists()


def test_evidence_owner_sql_keeps_final_fields_and_begin_ordinal(tmp_path):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.original_journal import OriginalJournal

    owner = EvidenceOwner(original_root=tmp_path / "owned", index_bytes=64 * 1024)
    try:
        first = {"uow": 0, "start_ns": 1}
        second = {"uow": 1, "start_ns": 2}
        first_token = owner.begin_sql(first)
        second_token = owner.begin_sql(second)
        second.update(end_ns=3, error="ReadFailure")
        owner.complete_sql(second_token, second)
        with pytest.raises(ValueError, match="pending"):
            owner.sql_reads[0]
        first.update(end_ns=4, snapshot="fixture:1", dispatched=True)
        owner.complete_sql(first_token, first)
        assert owner.sql_ordinal(first_token, first) == 0
        assert owner.sql_ordinal(second_token, second) == 1
        owner.retain("event-source", [{"id": UUID(int=5)}])
        owner.journal.seal({"owner": "actual EvidenceOwner"})
    finally:
        owner.journal.close()
    with OriginalJournal.open(
        tmp_path / "owned", budget=EvidenceBudget(), index_bytes=64 * 1024
    ) as reopened:
        assert reopened.sequence("sql")[0] == {
            "uow": 0,
            "start_ns": 1,
            "end_ns": 4,
            "snapshot": "fixture:1",
            "dispatched": True,
        }
        assert reopened.sequence("sql")[1]["error"] == "ReadFailure"
        assert reopened.sequence("operand:event-source")[0] == [{"id": UUID(int=5)}]


@pytest.mark.parametrize("mutation", ["directory", "body", "log", "body-bytes", "log-bytes"])
def test_replaced_owned_paths_never_authorize_sealing(tmp_path, mutation):
    import os

    with journal(tmp_path) as owner:
        owner.append("sql", {"uow": 0})
        if mutation == "directory":
            owner.root.rename(tmp_path / "displaced")
            owner.root.mkdir(mode=0o700)
        elif mutation.endswith("-bytes"):
            member = owner.root / (
                "000000.bin" if mutation == "body-bytes" else "occurrences.jsonl"
            )
            with member.open("r+b") as stream:
                stream.write(b"!")
        else:
            member = owner.root / ("000000.bin" if mutation == "body" else "occurrences.jsonl")
            member.rename(member.with_suffix(".displaced"))
            fd = os.open(member, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        with pytest.raises(ValueError, match=r"identity|bytes"):
            owner.seal({})
        assert not (owner.root / "manifest.json").exists()


def test_foreign_manifest_is_never_removed_on_failed_seal(tmp_path):
    with journal(tmp_path) as owner:
        owner.append("sql", {})
        target = owner.root / "manifest.json"
        target.write_bytes(b"unrelated existing member")
        target.chmod(0o600)
        with pytest.raises(FileExistsError):
            owner.seal({})
        assert target.read_bytes() == b"unrelated existing member"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "dispatch-error", "preflight-error"])
async def test_actual_repository_dispatch_uses_durable_begin_and_final_original(
    tmp_path, monkeypatch, mode
):
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.observer_session import ObserverSession
    from sqlalchemy import create_engine, literal, select
    from sqlalchemy.engine import Connection, IteratorResult
    from sqlalchemy.engine.result import SimpleResultMetaData
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_evaluation_configuration_repository import (
        DBEvaluationConfigurationRepository,
    )

    owner = EvidenceOwner(original_root=tmp_path / "actual", index_bytes=64 * 1024)
    engine = create_engine("sqlite://")
    original = Connection.execute

    def driver(connection, statement, parameters=None, *args, **kwargs):
        # These files must exist before either externally supplied read result.
        assert (owner.journal.root / "occurrences.jsonl").stat().st_size > 0
        assert len(owner.sql_reads) == 1
        with pytest.raises(ValueError, match="pending"):
            owner.sql_reads[0]
        if statement.get_execution_options().get("c2c_preflight"):
            if mode == "preflight-error":
                raise OSError("fixture preflight failure")
            values = {
                "row_count": 1,
                "max_bytes": 40,
                "total_bytes": 40,
                "read_only": "on",
                "isolation": "repeatable read",
                "snapshot": "fixture:1",
            }
            return original(connection, select(*[literal(v).label(k) for k, v in values.items()]))
        if mode == "dispatch-error":
            raise OSError("fixture dispatch failure")
        return IteratorResult(SimpleResultMetaData(["body"]), iter([({"opaque": UUID(int=7)},)]))

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    try:
        async with AsyncSession(sync_session_class=Bound) as session:
            repository = DBEvaluationConfigurationRepository(session)
            if mode == "ok":
                assert await repository.get_version(
                    OwnerScope.personal("fixture"), "suite", UUID(int=1)
                ) == {"opaque": UUID(int=7)}
            else:
                with pytest.raises(OSError, match="fixture"):
                    await repository.get_version(
                        OwnerScope.personal("fixture"), "suite", UUID(int=1)
                    )
        row = owner.sql_reads[0]
        assert row["end_ns"] >= row["start_ns"]
        if mode == "ok":
            assert row["dispatched"] is True
            assert owner.originals["version-source"][0]["read"]["sql_index"] == 0
        else:
            assert row["error"] == "OSError"
    finally:
        engine.dispose()
        owner.journal.close()


def test_partial_acquisition_chunks_remain_ordered_and_close_once(tmp_path):
    from scripts.execution_capacity.original_journal import OriginalJournal

    with journal(tmp_path) as owner:
        token = owner.begin("transports", {"request": ["fixture"]})
        owner.note(token, {"stream": "stdout", "offset": 0, "data": b"first"})
        owner.note(token, {"stream": "stdout", "offset": 5, "data": b"last"})
        owner.complete(token, {"stdout": b"firstlast", "error": None})
        with pytest.raises(ValueError, match="completed"):
            owner.note(token, {"data": b"late"})
        owner.seal({})
        root = owner.root
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024) as reopened:
        assert list(reopened.notes("transports", 0)) == [
            {"stream": "stdout", "offset": 0, "data": b"first"},
            {"stream": "stdout", "offset": 5, "data": b"last"},
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_actual_object_owner_is_pending_before_delegate_and_retains_final(tmp_path, failed):
    from scripts.execution_capacity.evidence_objects import EvidenceObjects
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    from app.domain.external.object_storage import BoundedObjectBytes

    owner = EvidenceOwner(original_root=tmp_path / "actual", index_bytes=64 * 1024)

    class Delegate:
        async def get_bounded_bytes(self, key, limit, *, observed):
            assert key == "private-key"
            assert limit == 16
            assert len(objects.originals) == 1
            with pytest.raises(ValueError, match="pending"):
                objects.originals[0]
            if failed:
                raise OSError("fixture object failure")
            observed(0, b"original")
            return BoundedObjectBytes(data=b"original", truncated=False)

    objects = EvidenceObjects(Delegate(), owner.budget.child(), object_limit=16, evidence=owner)
    try:
        if failed:
            with pytest.raises(OSError, match="fixture"):
                await objects.get_bytes("private-key")
        else:
            assert await objects.get_bytes("private-key") == b"original"
        result = objects.originals[0]
        assert result["end_ns"] >= result["start_ns"]
        assert result["data"] == (None if failed else b"original")
        assert result["error"] == ("OSError" if failed else None)
    finally:
        owner.journal.close()


@pytest.mark.parametrize("data", [b"one\n", b"0123456789"])
def test_actual_transport_journals_each_observed_chunk_before_completion(tmp_path, data):
    import os

    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.evidence_transport import EvidenceTransport

    owner = EvidenceOwner(original_root=tmp_path / "actual", index_bytes=64 * 1024)

    class Process:
        def __init__(self):
            assert len(transport.originals) == 1
            with pytest.raises(ValueError, match="pending"):
                transport.originals[0]
            self.stdout, self.stderr = self.pipe(data), self.pipe(b"")

        @staticmethod
        def pipe(value):
            reader, writer = os.pipe()
            os.write(writer, value)
            os.close(writer)
            return os.fdopen(reader, "rb")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            assert list(owner.journal.notes("transports", 0)) == [
                {"stream": "stdout", "offset": 0, "data": data}
            ]
            return 0

        def kill(self):
            pytest.fail("exited fixture process should not be killed")

    transport = EvidenceTransport(
        owner.budget.child(),
        evidence=owner,
        process_factory=lambda *a, **k: Process(),
        frame_limit=8,
    )
    try:
        if data == b"one\n":
            assert transport("exec", "fixture", "read") == data
        else:
            with pytest.raises(EvidenceQuotaError):
                transport("exec", "fixture", "read")
        record = transport.originals[0]
        assert record["stdout"] == data
        assert record["stderr"] == b""
        assert record["end_ns"] >= record["start_ns"]
    finally:
        owner.journal.close()


def test_original_slice_does_not_read_or_allocate_all_observations(tmp_path):
    with journal(tmp_path) as owner:
        for ordinal in range(20):
            owner.append("sql", {"ordinal": ordinal})
        limit = owner.budget.bytes_limit
        owner.budget.bytes_limit = owner.budget.bytes
        try:
            selected = owner.sequence("sql")[::-3]
            assert len(selected) == 7
        finally:
            owner.budget.bytes_limit = limit
        assert [row["ordinal"] for row in selected] == [19, 16, 13, 10, 7, 4, 1]


def test_actual_read_worker_can_durably_append_chunks_before_return(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with journal(tmp_path) as owner:
        token = owner.begin("objects", {"key": "fixture"})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(owner.note, token, {"offset": 0, "data": b"thread bytes"}).result()
        assert list(owner.notes("objects", 0)) == [{"offset": 0, "data": b"thread bytes"}]
        owner.complete(token, {"data": b"thread bytes", "error": None})


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_actual_object_adapter_retains_observed_prefix_before_next_read(tmp_path, failed):
    from types import SimpleNamespace

    from scripts.execution_capacity.evidence_objects import EvidenceObjects
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    from app.infrastructure.adapters.object_storage import MinioObjectStorageAdapter

    owner = EvidenceOwner(original_root=tmp_path / "actual", index_bytes=64 * 1024)

    class Response:
        started = closed = released = False

        def read(self, count):
            if not self.started:
                self.started = True
                return b"abc"
            assert list(owner.journal.notes("objects", 0)) == [{"offset": 0, "data": b"abc"}]
            if failed:
                raise OSError("fixture interrupted object")
            return b""

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    response = Response()
    storage = SimpleNamespace(
        bucket="fixture", client=SimpleNamespace(get_object=lambda *a: response)
    )
    objects = EvidenceObjects(
        MinioObjectStorageAdapter(storage), owner.budget.child(), evidence=owner, object_limit=16
    )
    try:
        if failed:
            with pytest.raises(OSError, match="interrupted"):
                await objects.get_bytes("fixture")
        else:
            assert await objects.get_bytes("fixture") == b"abc"
        assert response.closed
        assert response.released
        assert list(owner.journal.notes("objects", 0)) == [{"offset": 0, "data": b"abc"}]
    finally:
        owner.journal.close()


def test_actual_close_originals_seals_existing_owner_then_reopens_versioned_roots(tmp_path):
    from scripts.execution_capacity.attempt import AttemptLedger, digest
    from scripts.execution_capacity.c2c_export import close_originals
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.original_shards import OriginalView

    config = {"seal_id": "fixture", "protocol_id": "fixture", "identity": {"attempt_id": "fixture"}}
    plan = {
        "identity": config["identity"],
        "protocol_id": "fixture",
        "config_digest": digest(config),
    }
    owner = EvidenceOwner(original_root=tmp_path / "c2c-originals", index_bytes=64 * 1024)
    try:
        owner.retain("event-source", [{"id": UUID(int=17), "data": b"retained"}])
        cleanup = {
            "source_inventory_digest": digest({}),
            "writer_quiescence_digest": digest({}),
            "complete_quiescence_digest": digest({}),
            "observer_closed_ns": 1,
        }
        roots = {
            "cleanup": cleanup,
            "operands": owner.originals,
            "sql": owner.sql_reads,
            "objects": owner.journal.sequence("objects"),
            "transports": owner.journal.sequence("transports"),
        }
        with AttemptLedger.create(tmp_path / "operations", plan) as ledger:
            ledger.evidence_owner = owner
            final = close_originals(tmp_path, config, ledger, roots, budget=owner.budget)
            assert ledger.count("c2c-private-final") == 1
            assert final["unit"]["kind"] == "base"
        with OriginalView.open(
            tmp_path / "c2c-originals", budget=EvidenceBudget(), index_bytes=64 * 1024
        ) as view:
            assert view.manifest["schema"] == 2
            reopened = view.materialize()
            assert reopened["cleanup"] == cleanup
            assert reopened["operands"]["event-source"][0] == [
                {"id": UUID(int=17), "data": b"retained"}
            ]
            assert len(reopened["sql"]) == 0
            assert set(reopened["operands"]) == set(owner.originals)
    finally:
        owner.journal.close()


def test_equal_typed_collections_share_body_bytes_but_keep_every_occurrence(tmp_path):
    import json

    with journal(tmp_path) as owner:
        for _ in range(5):
            owner.append("operand:event-source", [{"id": UUID(int=9), "data": b"body"}])
        assert len(owner.sequence("operand:event-source")) == 5
        rows = [
            json.loads(line)
            for line in (owner.root / "occurrences.jsonl").read_bytes().splitlines()
        ]
        completions = [row for row in rows if row["kind"] == "end"]
        assert [row["ordinal"] for row in completions] == [0, 1, 2, 3, 4]
        assert len({row["body"]["offset"] for row in completions}) == 1
        owner.seal({})


def test_body_locator_collision_never_aliases_different_typed_values(tmp_path, monkeypatch):
    from scripts.execution_capacity.original_journal import OriginalJournal

    monkeypatch.setattr(OriginalJournal, "_lookup_key", staticmethod(lambda digest: "collision"))
    with journal(tmp_path) as owner:
        owner.append("sql", {"value": UUID(int=8)})
        owner.append("sql", {"value": str(UUID(int=8))})
        owner.append("sql", {"value": True})
        owner.append("sql", {"value": 1})
        owner.seal({})
        root = owner.root
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024) as reopened:
        values = [row["value"] for row in reopened.sequence("sql")]
        assert [type(value) for value in values] == [UUID, str, bool, int]
        assert values[:2] == [UUID(int=8), str(UUID(int=8))]


def test_candidate_mutation_cannot_commit_different_original_bytes(tmp_path, monkeypatch):
    import os

    read = os.pread
    with journal(tmp_path) as owner:
        changed = False

        def corrupt(fd, length, offset):
            nonlocal changed
            if not changed and os.fstat(fd).st_ino == (owner.root / "body.pending").stat().st_ino:
                changed = True
                os.pwrite(fd, b"!", 0)
            return read(fd, length, offset)

        monkeypatch.setattr(os, "pread", corrupt)
        with pytest.raises(ValueError, match="candidate"):
            owner.append("sql", {"uow": 0})
        with pytest.raises(ValueError, match="invalid"):
            owner.seal({})


@pytest.mark.parametrize("mutation", ["ordinal", "duplicate-end", "namespace", "family-count"])
def test_recomputed_wire_hash_does_not_bypass_original_closure(tmp_path, mutation):
    import json
    from hashlib import sha256

    from scripts.execution_capacity.original_journal import OriginalJournal

    with journal(tmp_path) as owner:
        owner.append("sql", {"uow": 0})
        owner.seal({})
        root = owner.root
    rows = [json.loads(line) for line in (root / "occurrences.jsonl").read_bytes().splitlines()]
    manifest = json.loads((root / "manifest.json").read_bytes())
    if mutation == "ordinal":
        rows[-1]["ordinal"] = 7
    elif mutation == "duplicate-end":
        rows.append({**rows[-1], "sequence": len(rows)})
        manifest["records"] += 1
    elif mutation == "namespace":
        rows[-1]["body"]["namespace"] = "verified-base"
    else:
        manifest["families"]["sql"] = 2
    raw = b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows)
    (root / "occurrences.jsonl").write_bytes(raw)
    manifest.update(log_bytes=len(raw), log_sha256=sha256(raw).hexdigest())
    manifest["log_chunks"][0].update(bytes=len(raw), sha256=sha256(raw).hexdigest())
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=r"original|duplicate"):
        OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024)


def test_logical_alias_is_root_scoped_and_distinct_equal_nodes_stay_distinct(tmp_path):
    from scripts.execution_capacity.original_journal import OriginalJournal
    from scripts.execution_capacity.safe_projection import typed_digest

    shared = {"values": [UUID(int=3), Decimal("1.00"), True, 1, b"raw"]}
    original = {"first": shared, "alias": shared, "equal": {"values": list(shared["values"])}}
    expected = typed_digest(original, budget=EvidenceBudget())
    with journal(tmp_path) as owner:
        owner.append("sql", original)
        owner.append("sql", original)
        owner.seal({})
        root = owner.root
    with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=64 * 1024) as owner:
        first, second = owner.sequence("sql")
        assert first["first"] is first["alias"]
        assert first["first"] == first["equal"]
        assert first["first"] is not first["equal"]
        assert first["first"] is not second["first"]
        assert list(first) == ["first", "alias", "equal"]
        assert typed_digest(first, budget=EvidenceBudget()) == expected


def test_cleanup_graph_keeps_dataclass_alias_without_class_decode(tmp_path):
    from dataclasses import dataclass

    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.original_shards import OriginalView

    @dataclass
    class Source:
        identity: UUID

    source = Source(UUID(int=8))
    owner = EvidenceOwner(original_root=tmp_path / "originals", index_bytes=64 * 1024)
    try:
        owner.finish_originals(
            {
                "cleanup": {"source": source, "quiescence": {"source": source}},
                "operands": owner.originals,
                "sql": owner.sql_reads,
                "objects": owner.journal.sequence("objects"),
                "transports": owner.journal.sequence("transports"),
            },
            {},
        )
    finally:
        owner.journal.close()
    with OriginalView.open(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=64 * 1024
    ) as view:
        cleanup = view.materialize()["cleanup"]
        assert cleanup["source"] == {"identity": UUID(int=8)}
        assert cleanup["source"] is cleanup["quiescence"]["source"]
        assert type(cleanup["source"]) is dict


def test_scoped_graph_decoder_restores_only_completed_local_references():
    from scripts.execution_capacity.original_journal import _decode

    raw = b'["graph",2,["list",0,[["dict",1,[]],["ref",1]]],3]'
    actual = _decode(raw, EvidenceBudget())
    assert actual == [{}, {}]
    assert actual[0] is actual[1]


@pytest.mark.parametrize(
    "raw",
    [
        b'["graph",2,["ref",0],1]',
        b'["graph",2,["list",0,[["ref",0]]],2]',
        b'["graph",2,["list",0,[["dict",1,[]],["dict",1,[]]]],3]',
        b'["graph",2,["list",0,[["dict",1,[]],["ref",["foreign",1]]]],3]',
        b'["graph",2,["list",0,[["dict",1,[]],["ref",true]]],3]',
        b'["graph",2,["dict",0,[]],2]',
        b'["graph",2,["dict",2,[]],1]',
        b'["graph",2,["dict",0,[["x",["scalar",1]],["x",["scalar",2]]]],3]',
    ],
)
def test_scoped_graph_rejects_missing_cycle_duplicate_foreign_and_count_errors(raw):
    from scripts.execution_capacity.original_journal import _decode

    with pytest.raises(ValueError, match="original"):
        _decode(raw, EvidenceBudget())


def test_cyclic_root_is_rejected_before_nesting_quota(tmp_path):
    value = []
    value.append(value)
    with journal(tmp_path) as owner:
        with pytest.raises(ValueError, match="cyclic"):
            owner.append("sql", value)
        assert not (owner.root / "manifest.json").exists()


def test_reference_cannot_hide_expanded_graph_depth():
    from scripts.execution_capacity.attempt import encode
    from scripts.execution_capacity.original_journal import _decode

    first = ["scalar", "leaf"]
    for ordinal in range(32, 0, -1):
        first = ["list", ordinal, [first]]
    later = ["ref", 1]
    for ordinal in range(72, 32, -1):
        later = ["list", ordinal, [later]]
    raw = encode(["graph", 2, ["list", 0, [first, later]], 75])
    with pytest.raises(ValueError, match="nesting"):
        _decode(raw, EvidenceBudget())


def test_graph_depth_boundary_and_alias_shape_survive_physical_sharing(tmp_path):
    shared = []
    deepest = shared
    for _ in range(63):
        deepest = [deepest]
    with journal(tmp_path) as owner:
        owner.append("sql", [deepest, shared])
        value = owner.sequence("sql")[0]
        deepest_read = value[0]
        for _ in range(63):
            deepest_read = deepest_read[0]
        assert deepest_read is value[1]
        owner.append("sql", [shared, shared])
        owner.append("sql", [[], []])
        alias = owner.sequence("sql")[1]
        separate = owner.sequence("sql")[2]
        assert alias == separate
        assert alias[0] is alias[1]
        assert separate[0] is not separate[1]
        owner.seal({})


def test_segmented_raw_streams_cross_file_boundaries_and_reopen_every_occurrence(tmp_path):
    from scripts.execution_capacity.original_journal import OriginalJournal

    expected = [{"ordinal": ordinal, "value": "x" * 700} for ordinal in range(25)]
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024, chunk_bytes=4096
    ) as owner:
        for row in expected:
            owner.append("sql", row)
        manifest = owner.seal({})
        assert len(manifest["body_chunks"]) > 1
        assert len(manifest["log_chunks"]) > 1
        assert all(row["bytes"] <= 4096 for row in manifest["body_chunks"] + manifest["log_chunks"])
    with OriginalJournal.open(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
    ) as reopened:
        assert list(reopened.sequence("sql")) == expected


@pytest.mark.parametrize("stream", ["body", "log"])
def test_missing_late_segment_cannot_reopen_prefix_as_complete(tmp_path, stream):
    from scripts.execution_capacity.original_journal import OriginalJournal

    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024, chunk_bytes=4096
    ) as owner:
        for ordinal in range(25):
            owner.append("sql", {"ordinal": ordinal, "value": "x" * 700})
        manifest = owner.seal({})
    final = manifest[stream + "_chunks"][-1]["ordinal"]
    name = f"{final:06}.bin" if stream == "body" else f"{final:06}.jsonl"
    (tmp_path / "originals" / name).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        OriginalJournal.open(
            tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
        )


def test_new_segment_name_is_durable_before_acquisition_returns(tmp_path, monkeypatch):
    import os

    from scripts.execution_capacity.original_journal import OriginalJournal

    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024, chunk_bytes=4096
    ) as owner:
        real_sync = os.fsync

        def fail_log_directory(fd):
            if fd == owner.directory and (owner.root / "000001.jsonl").exists():
                raise OSError("injected segment directory durability")
            return real_sync(fd)

        monkeypatch.setattr(os, "fsync", fail_log_directory)

        def begin_until_rollover():
            for ordinal in range(25):
                owner.begin("sql", {"ordinal": ordinal})
                assert not (owner.root / "000001.jsonl").exists(), (
                    "begin returned before directory durability"
                )

        with pytest.raises(OSError, match="segment directory"):
            begin_until_rollover()
        assert not (owner.root / "manifest.json").exists()
        with pytest.raises(ValueError, match="invalid"):
            owner.seal({})


def test_initial_segment_fsync_failure_closes_open_file_descriptor(tmp_path, monkeypatch):
    import os

    from scripts.execution_capacity.original_journal import OriginalJournal

    opened = []
    real_file = OriginalJournal._file
    real_sync = os.fsync

    def file(owner, name, flags):
        fd = real_file(owner, name, flags)
        opened.append(fd)
        return fd

    def fail(fd):
        if fd in opened:
            raise OSError("initial segment durability")
        return real_sync(fd)

    monkeypatch.setattr(OriginalJournal, "_file", file)
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="initial segment"):
        journal(tmp_path)
    assert opened
    for fd in opened:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)


@pytest.mark.parametrize("mutation", ["boolean", "gap", "short", "path", "extra"])
def test_segment_manifest_rejects_ambiguous_or_unclosed_members(tmp_path, mutation):
    import json

    from scripts.execution_capacity.original_journal import OriginalJournal

    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024, chunk_bytes=4096
    ) as owner:
        for ordinal in range(25):
            owner.append("sql", {"ordinal": ordinal, "value": "x" * 700})
        manifest = owner.seal({})
    if mutation == "boolean":
        manifest["body_chunks"][0]["ordinal"] = False
    elif mutation == "gap":
        manifest["log_chunks"][1]["ordinal"] = 2
    elif mutation == "short":
        manifest["body_chunks"][0]["bytes"] -= 1
    elif mutation == "path":
        manifest["body_chunks"][0]["path"] = "../foreign"
    else:
        extra = tmp_path / "originals" / "999999.bin"
        extra.write_bytes(b"foreign")
        extra.chmod(0o600)
    (tmp_path / "originals" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=r"segment|membership"):
        OriginalJournal.open(
            tmp_path / "originals", budget=EvidenceBudget(), index_bytes=256 * 1024
        )
