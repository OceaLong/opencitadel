"""Finite private collection candidates, lifecycle and original row identity."""

from uuid import UUID

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.original_journal import OriginalJournal


def test_collection_producer_is_durable_before_rows_and_snapshot_survives_reopen(tmp_path):
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024, chunk_bytes=4096
    ) as owner:
        parent = owner.begin("operand:query-rows", {"name": "fixture-query"})
        writer = owner.begin_collection(parent, "rows")
        with pytest.raises(ValueError, match="pending"):
            owner.seal({})
        for ordinal in range(40):
            writer.append({"ordinal": ordinal, "id": UUID(int=ordinal), "data": b"x" * 400})
        rows = writer.complete()
        assert len(rows) == 40
        assert rows[39]["id"] == UUID(int=39)
        with pytest.raises(ValueError, match="complete"):
            writer.append({"ordinal": 40})
        owner.complete(parent, {"name": "fixture-query", "rows": rows})
        owner.seal({})
    with OriginalJournal.open(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as view:
        actual = view.sequence("operand:query-rows")[0]["rows"]
        assert len(actual) == 40
        assert actual[0] == {"ordinal": 0, "id": UUID(int=0), "data": b"x" * 400}
        assert actual[39]["ordinal"] == 39


def test_equal_collections_keep_distinct_producer_and_occurrence_identity(tmp_path):
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        parents, handles = [], []
        for ordinal in range(2):
            parent = owner.begin("operand:query-rows", {"name": "fixture", "ordinal": ordinal})
            writer = owner.begin_collection(parent, "rows")
            writer.append({"id": UUID(int=9)})
            rows = writer.complete()
            owner.complete(parent, {"rows": rows, "alias": rows})
            parents.append(parent)
            handles.append(rows)
        assert handles[0].producer_ordinal != handles[1].producer_ordinal
        assert handles[0].body_descriptor == handles[1].body_descriptor
        owner.seal({})
        first, second = owner.sequence("operand:query-rows")
        assert first["rows"] is first["alias"]
        assert first["rows"] is not second["rows"]


def test_foreign_or_unfinished_collection_cannot_complete_parent(tmp_path):
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        first = owner.begin("operand:query-rows", {"name": "first"})
        second = owner.begin("operand:query-rows", {"name": "second"})
        writer = owner.begin_collection(first, "rows")
        writer.append({"row": 0})
        with pytest.raises(ValueError, match="complete"):
            owner.complete(first, {"rows": writer})
        # Invalid candidate use leaves a failed acquisition prefix, not a clean closure.
        assert not (owner.root / "manifest.json").exists()
        assert second.ordinal == 1


def _completed(owner, name="query", value=None):
    parent = owner.begin("operand:query-rows", {"name": name})
    writer = owner.begin_collection(parent, "rows")
    writer.append({"value": 1} if value is None else value)
    return parent, writer.complete()


def test_foreign_cursor_cannot_hide_behind_same_local_producer_ordinal(tmp_path):
    with (
        OriginalJournal.create(
            tmp_path / "a", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as a,
        OriginalJournal.create(
            tmp_path / "b", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as b,
    ):
        parent, local = _completed(a)
        _, foreign = _completed(b)
        with pytest.raises(ValueError, match="foreign"):
            a.complete(parent, {"local": local, "foreign": foreign})
        assert not (a.root / "manifest.json").exists()


def test_equal_body_cannot_be_bound_to_another_acquisition(tmp_path):
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        first, rows = _completed(owner, "first")
        second = owner.begin("operand:query-rows", {"name": "second"})
        with pytest.raises(ValueError, match="parent"):
            owner.complete(second, {"rows": rows})
        assert first.ordinal == 0


def _rewrite_log(root, mutate):
    from hashlib import sha256

    from scripts.acceptance.capacity_io import strict_json
    from scripts.execution_capacity.attempt import encode

    manifest = strict_json((root / "manifest.json").read_bytes())
    rows = [strict_json(row) for row in (root / "occurrences.jsonl").read_bytes().splitlines()]
    mutate(rows)
    for sequence, row in enumerate(rows):
        row["sequence"] = sequence
    raw = b"".join(encode(row) + b"\n" for row in rows)
    (root / "occurrences.jsonl").write_bytes(raw)
    digest = sha256(raw).hexdigest()
    manifest.update(records=len(rows), log_bytes=len(raw), log_sha256=digest)
    manifest["log_chunks"][0].update(bytes=len(raw), sha256=digest)
    (root / "manifest.json").write_bytes(encode(manifest))


@pytest.mark.parametrize(
    "mutation", ["swap-edge", "missing-edge", "extra-edge", "missing-row", "row-order"]
)
def test_recomputed_log_hash_cannot_authorize_rebound_or_incomplete_collection(tmp_path, mutation):
    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as owner:
        pending = []
        for name in ("one", "two"):
            parent = owner.begin("operand:query-rows", {"name": name})
            writer = owner.begin_collection(parent, "rows")
            writer.append({"value": 1})
            writer.append({"value": True})
            pending.append((parent, writer.complete()))
        for parent, rows in pending:
            owner.complete(parent, {"rows": rows})
        owner.seal({})

    def mutate(rows):
        parent = next(
            row for row in rows if row["family"] == "operand:query-rows" and row["kind"] == "end"
        )
        notes = [
            row
            for row in rows
            if row["family"] == "collections" and row["kind"] == "note" and row["ordinal"] == 0
        ]
        if mutation == "swap-edge":
            parent["edges"][0][1] = 1
        elif mutation == "missing-edge":
            del parent["edges"]
        elif mutation == "extra-edge":
            parent["edges"].append([99, 0])
        elif mutation == "missing-row":
            rows.remove(notes[0])
        else:
            notes[0]["body"], notes[1]["body"] = notes[1]["body"], notes[0]["body"]

    _rewrite_log(root, mutate)
    with pytest.raises(ValueError, match=r"original collection|original body reference"):
        OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=512 * 1024)


def test_collection_typed_commitment_and_fresh_descriptor_copy_match_list_oracle(tmp_path):
    from decimal import Decimal
    from hashlib import sha256

    from scripts.acceptance.capacity_io import copy_artifacts
    from scripts.acceptance.capacity_models import Artifact
    from scripts.execution_capacity.original_shards import OriginalView
    from scripts.execution_capacity.safe_projection import typed_digest

    root = tmp_path / "originals"
    expected = [{"v": value} for value in (UUID(int=5), Decimal("1.00"), b"bytes", None, 1, True)]
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as owner:
        parent = owner.begin("operand:query-rows", {"name": "typed"})
        writer = owner.begin_collection(parent, "rows")
        for row in expected:
            writer.append(row)
        rows = writer.complete()
        owner.complete(parent, {"rows": rows})
        owner.append("cleanup", {"rows": rows})
        owner.seal({}, original_roots=True)
    with OriginalView.open(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as view:
        actual = view.journal.sequence("operand:query-rows")[0]["rows"]
        digest = typed_digest(actual, budget=EvidenceBudget(), view=view)
        assert digest == typed_digest(expected, budget=EvidenceBudget())
        assert digest != typed_digest(list(reversed(expected)), budget=EvidenceBudget())
        with pytest.raises(ValueError, match="owner"):
            typed_digest(actual, budget=EvidenceBudget())
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
        copy_artifacts(descriptors, root, tmp_path / "copy")
    with pytest.raises(ValueError, match="closed"):
        typed_digest(actual, budget=EvidenceBudget(), view=view)
    with OriginalView.open(
        tmp_path / "copy", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as fresh:
        copied = fresh.journal.sequence("operand:query-rows")[0]["rows"]
        assert typed_digest(copied, budget=EvidenceBudget(), view=fresh) == digest
        with pytest.raises(ValueError, match="owner"):
            typed_digest(actual, budget=EvidenceBudget(), view=fresh)


@pytest.mark.parametrize("fault", [None, "invalid", "cancel"])
def test_discarded_leaf_collection_decode_has_one_bounded_scope_and_durable_prefix(
    tmp_path, monkeypatch, fault
):
    import asyncio
    from copy import deepcopy
    from hashlib import sha256

    import scripts.execution_capacity.original_journal as module
    from scripts.acceptance.capacity_io import strict_json

    root = tmp_path / "originals"
    budget = EvidenceBudget()
    original_decode = module._decode
    borrowed = []

    def observed_decode(raw, owner_budget, **kwargs):
        if kwargs.get("_borrowed_prepaid"):
            borrowed.append((len(raw), owner_budget.workspace_bytes))
            assert owner_budget.workspace_bytes >= len(raw) * 64
            if fault == "invalid":
                raise ValueError("fixture leaf decode rejected")
            if fault == "cancel":
                raise asyncio.CancelledError()
        return original_decode(raw, owner_budget, **kwargs)

    monkeypatch.setattr(module, "_decode", observed_decode)
    with OriginalJournal.create(root, budget=budget, index_bytes=512 * 1024) as owner:
        parent = owner.begin("operand:query-rows", {"name": "leaf"})
        writer = owner.begin_collection(parent, "rows")
        writer.append({"id": UUID(int=1), "raw": b"leaf"})
        prefix = sha256((root / "occurrences.jsonl").read_bytes()).hexdigest()
        index_lease = budget.workspace_bytes
        prior_work, prior_persistent = budget.workspace_work_bytes, budget.bytes
        if fault is not None:
            expected = ValueError if fault == "invalid" else asyncio.CancelledError
            with pytest.raises(expected):
                writer.complete()
            assert sha256((root / "occurrences.jsonl").read_bytes()).hexdigest() == prefix
            assert owner.index.count("note:collections:0") == 1
            assert owner.index.count("end:collections") == 0
            assert not (root / "manifest.json").exists()
        else:
            rows = writer.complete()
            assert rows[0] == {"id": UUID(int=1), "raw": b"leaf"}
            note = strict_json(owner.index.find("note:collections:0", "ordinal", "0"))
            wrong_body = deepcopy(note)
            wrong_body["body"]["sha256"] = "0" * 64
            with pytest.raises(ValueError, match="original body changed"):
                owner._record_value(wrong_body, _discarded_leaf=True)
            wrong_edge = deepcopy(note)
            wrong_edge["edges"] = [[0, 0]]
            with pytest.raises(ValueError, match="extra original collection edge"):
                owner._record_value(wrong_edge)
            owner.complete(parent, {"rows": rows})
            owner.seal({})
            assert len(borrowed) == 2  # store and independent validation
        assert budget.workspace_bytes == index_lease
        assert budget.workspace_work_bytes - prior_work == sum(size * 64 for size, _ in borrowed)
        assert budget.bytes > prior_persistent
        assert all(active >= size * 64 for size, active in borrowed)
    assert budget.workspace_bytes == 0
    if fault is None:
        with OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as fresh:
            assert fresh.sequence("operand:query-rows")[0]["rows"][0] == {
                "id": UUID(int=1),
                "raw": b"leaf",
            }


def test_discarded_leaf_malformed_collection_declaration_preserves_missing_edge_layer(tmp_path):
    from copy import deepcopy

    from scripts.acceptance.capacity_io import strict_json

    budget = EvidenceBudget()
    with OriginalJournal.create(
        tmp_path / "originals", budget=budget, index_bytes=512 * 1024
    ) as owner:
        parent = owner.begin("operand:query-rows", {"name": "edge-layer"})
        writer = owner.begin_collection(parent, "rows")
        writer.append({"value": 1})
        rows = writer.complete()
        note = strict_json(owner.index.find("note:collections:0", "ordinal", "0"))
        encoded_edges = []
        nested_body = owner._store({"nested": rows}, edges=encoded_edges)
        assert encoded_edges
        malformed = deepcopy(note)
        malformed["body"] = nested_body
        assert malformed.get("edges", []) == []
        index_lease = budget.workspace_bytes
        for borrowed in (False, True):
            with pytest.raises(ValueError, match="missing original collection edge"):
                owner._record_value(malformed, _discarded_leaf=borrowed)
            assert budget.workspace_bytes == index_lease
        nonleaf = deepcopy(note)
        nonleaf["edges"] = encoded_edges
        with pytest.raises(
            ValueError, match="discarded original value must have no collection edges"
        ):
            owner._record_value(nonleaf, _discarded_leaf=True)
        assert budget.workspace_bytes == index_lease
    assert budget.workspace_bytes == 0


@pytest.mark.parametrize("cancel", [False, True])
def test_late_leaf_decode_traceback_promotes_large_graph_without_double_admission(
    tmp_path, monkeypatch, cancel
):
    import asyncio
    from hashlib import sha256

    import scripts.execution_capacity.original_journal as module

    parent_budget = EvidenceBudget(bytes_limit=64 * 1024 * 1024, rows_limit=50_000)
    budget = parent_budget.child()
    original_decode = module._decode
    observed = []

    def late_decode(raw, owner_budget, **kwargs):
        value = original_decode(raw, owner_budget, **kwargs)
        if kwargs.get("_borrowed_prepaid") and type(value) is dict and "nodes" in value:
            observed.append((owner_budget.bytes, parent_budget.bytes, len(raw) * 64))
            if cancel:
                raise asyncio.CancelledError()
            raise ValueError("late leaf graph rejected")
        return value

    monkeypatch.setattr(module, "_decode", late_decode)
    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=budget, index_bytes=512 * 1024) as owner:
        token = owner.begin("operand:query-rows", {"name": "large-leaf"})
        writer = owner.begin_collection(token, "rows")
        writer.append({"nodes": list(range(1000))})
        prefix = sha256((root / "occurrences.jsonl").read_bytes()).hexdigest()
        index_lease = budget.workspace_bytes
        expected = asyncio.CancelledError if cancel else ValueError
        with pytest.raises(expected) as error:
            writer.complete()
        graph = None
        traceback = error.value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_code.co_name == "late_decode":
                graph = traceback.tb_frame.f_locals["value"]
            traceback = traceback.tb_next
        assert type(graph) is dict
        assert len(graph["nodes"]) == 1000
        child_before, parent_before, promoted = observed[0]
        assert (budget.bytes, parent_budget.bytes) == (
            child_before + promoted,
            parent_before + promoted,
        )
        assert budget.workspace_bytes == parent_budget.workspace_bytes == index_lease
        assert budget.workspace_work_bytes >= promoted
        assert sha256((root / "occurrences.jsonl").read_bytes()).hexdigest() == prefix
        assert owner.index.count("end:collections") == 0
    assert budget.workspace_bytes == parent_budget.workspace_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "row-error",
        "cardinality",
        "mixed",
        "interleaved",
        "close-error",
        "close-cancel",
        "output-append",
        "finish",
    ],
)
async def test_actual_inventory_queries_stream_to_owned_collection_before_next_driver_row(
    tmp_path, monkeypatch, failure
):
    import asyncio

    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.evidence_owner import EvidenceOwner
    from scripts.execution_capacity.inventory_sql import OWNERS, InventoryQueries, plain
    from scripts.execution_capacity.observer_session import ObserverSession
    from scripts.execution_capacity.original_collections import CollectionRows
    from sqlalchemy import create_engine, literal, select, text
    from sqlalchemy.engine import Connection, IteratorResult
    from sqlalchemy.engine.result import SimpleResultMetaData
    from sqlalchemy.ext.asyncio import AsyncResult, AsyncSession

    owner = EvidenceOwner(original_root=tmp_path / "actual", index_bytes=512 * 1024)
    owner.begin_cleanup()
    engine = create_engine("sqlite://")
    original_execute = Connection.execute
    expected = [{"stream_id": UUID(int=1)}, {"stream_id": UUID(int=2)}]

    def rows():
        for ordinal, row in enumerate(expected):
            query_count = owner.journal.index.count("begin:operand:query-rows")
            assert owner.journal.index.count("note:collections:" + str(query_count + 1)) == ordinal
            if failure == "interleaved" and ordinal == 0:
                # The first result is still suspended while another actual
                # ORM dispatch replaces the session's latest receipt.
                session.sync_session.execute(text(OWNERS))
            if (
                failure == "row-error" or (failure == "mixed" and query_count == 2)
            ) and ordinal == 1:
                raise OSError("fixture late read")
            yield (row["stream_id"],)

    def driver(connection, statement, parameters=None, *args, **kwargs):
        query_count = owner.journal.index.count("begin:operand:query-rows")
        assert query_count >= 1
        assert owner.journal.index.count("begin:collections") == query_count + 2
        if statement.get_execution_options().get("c2c_preflight"):
            values = {
                "row_count": 3 if failure == "cardinality" else 2,
                "max_bytes": 100,
                "total_bytes": 200,
                "read_only": "on",
                "isolation": "repeatable read",
                "snapshot": "fixture:1",
            }
            return original_execute(
                connection, select(*[literal(v).label(k) for k, v in values.items()])
            )
        return IteratorResult(SimpleResultMetaData(["stream_id"]), rows())

    monkeypatch.setattr(Connection, "execute", driver)
    if failure in {"close-error", "close-cancel"}:
        original_close = AsyncResult.close

        async def close_with_failure(result):
            await original_close(result)
            if failure == "close-cancel":
                raise asyncio.CancelledError()
            raise OSError("fixture result close failed")

        monkeypatch.setattr(AsyncResult, "close", close_with_failure)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    try:
        async with AsyncSession(sync_session_class=Bound) as session:
            query = InventoryQueries(session)
            if failure in {"output-append", "finish"}:

                def cancel(*args):
                    raise asyncio.CancelledError()

                if failure == "output-append":
                    monkeypatch.setattr(query.reads, "append", cancel)
                    with pytest.raises(asyncio.CancelledError):
                        await query.rows("owners", OWNERS)
                else:
                    await query.rows("owners", OWNERS)
                    monkeypatch.setattr(query.originals, "complete", cancel)
                    with pytest.raises(asyncio.CancelledError):
                        query.finish_outputs()
                with pytest.raises(ValueError, match="aborted"):
                    query.finish_outputs()
                with pytest.raises(ValueError, match="not accepting"):
                    await query.rows("after-cancel", OWNERS)
                assert owner.journal.index.count("begin:operand:query-rows") == 1
                assert owner.journal.index.count("end:operand:query-rows") == 1
                assert owner.journal.index.find("end:collections", "ordinal", "1") is None
                return
            if failure == "mixed":
                await query.rows("first", OWNERS)
                with pytest.raises(OSError, match="fixture late read"):
                    await query.rows("second", OWNERS)
                await query.rows("third", OWNERS)
                query.finish_outputs()
                assert [r["name"] for r in query.reads] == ["first", "second", "third"]
                assert [r["name"] for r in query.originals] == ["first", "third"]
                assert [r["ordinal"] for r in query.originals] == [0, 1]
                assert query.originals[0]["read"] == query.reads[0]
                assert query.originals[1]["read"] == query.reads[2]
                assert query.reads[1]["error"] == "OSError"
                assert owner.journal.index.count("note:collections:3") == 1
                assert owner.journal.index.find("end:collections", "ordinal", "3") is None
                assert owner.journal.index.count("end:operand:query-rows") == 3
                assert owner.journal.index.count("end:sql") == 3
                assert (
                    query.reads[0]["start_ns"]
                    < query.reads[1]["start_ns"]
                    < query.reads[2]["start_ns"]
                )
                return
            if failure == "interleaved":
                with pytest.raises(ValueError, match="dispatch original changed during result"):
                    await query.rows("owners", OWNERS)
                query.finish_outputs()
                assert owner.journal.index.count("end:sql") == 2
                assert len(query.originals) == 0
                assert query.reads[0]["error"] == "ValueError"
                return
            if failure in {"close-error", "close-cancel"}:
                raised = asyncio.CancelledError if failure == "close-cancel" else OSError
                with pytest.raises(raised):
                    await query.rows("owners", OWNERS)
                assert owner.journal.index.count("end:sql") == 1
                assert len(query.originals) == 0
                if failure == "close-cancel":
                    with pytest.raises(ValueError, match="aborted"):
                        query.finish_outputs()
                    assert owner.journal.index.count("end:operand:query-rows") == 0
                else:
                    query.finish_outputs()
                    assert query.reads[0]["error"] == "OSError"
                    assert owner.journal.index.count("end:operand:query-rows") == 1
                return
            if failure is not None:
                with pytest.raises(
                    OSError if failure == "row-error" else ValueError,
                    match=r"fixture late read|cardinality",
                ):
                    await query.rows("owners", OWNERS)
                query.finish_outputs()
                assert len(query.reads) == 1
                assert len(query.originals) == 0
                retained = owner.originals["query-rows"][0]
                assert retained["read"]["error"] == (
                    "OSError" if failure == "row-error" else "ValueError"
                )
                assert retained["read"]["end_ns"] >= retained["read"]["start_ns"]
                assert owner.journal.index.count("note:collections:2") == (
                    1 if failure == "row-error" else 2
                )
                assert not (owner.journal.root / "manifest.json").exists()
                return
            actual = await query.rows("owners", OWNERS)
            assert type(actual) is CollectionRows
            assert list(actual) == expected
            query.finish_outputs()
            assert query.reads[0]["result_digest"] == canonical_digest(plain(expected))
            assert list(query.originals[0]["rows"]) == list(actual)
            retained = owner.originals["query-rows"][0]
            assert list(retained["rows"]) == expected
            assert retained["sql_index"] == 0
            assert retained["read"]["end_ns"] >= retained["read"]["start_ns"]
        owner.journal.complete(
            owner._cleanup_token, {"reads": query.reads, "originals": query.originals}
        )
        owner.journal.seal({})
    finally:
        engine.dispose()
        owner.journal.close()
    with OriginalJournal.open(
        tmp_path / "actual", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as fresh:
        assert list(fresh.sequence("operand:query-rows")[0]["rows"]) == expected


def test_plain_collection_preserves_original_conversion_and_committed_mode(tmp_path):
    from datetime import date
    from decimal import Decimal

    from scripts.execution_capacity.inventory_sql import plain
    from scripts.execution_capacity.original_shards import OriginalView
    from scripts.execution_capacity.safe_projection import typed_digest

    values = [{"z": UUID(int=8), "a": [Decimal("1.00"), b"raw", date(2026, 9, 7), True, 1, None]}]
    expected = plain(values)
    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as owner:
        parent, rows = _completed(owner, value=values[0])
        converted = plain(rows)
        assert list(converted) == expected
        owner.complete(parent, {"raw": rows, "plain": converted, "alias": converted})
        owner.append("cleanup", {"raw": rows, "plain": converted})
        owner.seal({}, original_roots=True)
    with OriginalView.open(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as view:
        saved = view.journal.sequence("operand:query-rows")[0]
        assert saved["raw"] is not saved["plain"]
        assert saved["plain"] is saved["alias"]
        assert list(saved["plain"]) == expected
        assert list(saved["raw"]) == values
        assert typed_digest(saved["plain"], budget=EvidenceBudget(), view=view) == typed_digest(
            expected, budget=EvidenceBudget()
        )
        assert typed_digest(saved["raw"], budget=EvidenceBudget(), view=view) != typed_digest(
            expected, budget=EvidenceBudget()
        )


def test_plain_never_stringifies_unknown_or_nested_cursor(tmp_path):
    from collections.abc import Sequence

    from scripts.execution_capacity.inventory_sql import plain

    class Foreign(Sequence):
        def __len__(self):
            return 1

        def __getitem__(self, ordinal):
            return "unowned"

    with pytest.raises(TypeError, match="collection"):
        plain(Foreign())
    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        _, rows = _completed(owner)
        with pytest.raises(TypeError, match="collection"):
            plain({"nested": rows})
    with pytest.raises(ValueError, match="closed"):
        plain(rows)


def test_collection_candidate_has_no_authority_before_durable_completion_record(
    tmp_path, monkeypatch
):
    from scripts.execution_capacity.original_collections import CollectionRows

    with OriginalJournal.create(
        tmp_path / "originals", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        parent = owner.begin("operand:query-rows", {"name": "candidate"})
        writer = owner.begin_collection(parent, "rows")
        writer.append({"row": 1})
        commit = owner._commit_record
        checked = []

        def intercept(kind, family, ordinal, body, edges=()):
            if family == "collections" and kind == "end":
                candidate = CollectionRows(owner, ordinal, 1, body)
                with pytest.raises(ValueError, match="complete"):
                    len(candidate)
                checked.append(True)
            return commit(kind, family, ordinal, body, edges)

        monkeypatch.setattr(owner, "_commit_record", intercept)
        rows = writer.complete()
        assert checked == [True]
        assert len(rows) == 1


@pytest.mark.parametrize("mode", ["plain-json-v2", None, "raw"])
def test_plain_collection_mode_is_closed_and_committed_in_occurrence_edges(tmp_path, mode):
    from scripts.execution_capacity.inventory_sql import plain

    root = tmp_path / "originals"
    with OriginalJournal.create(root, budget=EvidenceBudget(), index_bytes=512 * 1024) as owner:
        parent, rows = _completed(owner, value={"id": UUID(int=7)})
        owner.complete(parent, {"plain": plain(rows)})
        owner.seal({})

    def mutate(records):
        parent = next(
            row for row in records if row["family"] == "operand:query-rows" and row["kind"] == "end"
        )
        if mode is None:
            parent["edges"][0].pop()
        else:
            parent["edges"][0][2] = mode

    _rewrite_log(root, mutate)
    with pytest.raises(ValueError, match="original collection edge"):
        OriginalJournal.open(root, budget=EvidenceBudget(), index_bytes=512 * 1024)


def test_plain_collection_expands_nested_original_rows_with_explicit_owner(tmp_path):
    from decimal import Decimal

    from scripts.acceptance.capacity_io import canonical_digest
    from scripts.execution_capacity.inventory_sql import plain
    from scripts.execution_capacity.original_collections import CollectionRows, PlainCollectionRows
    from scripts.execution_capacity.original_plain import canonical_original_digest, plain_graph

    raw_values = [{"id": UUID(int=7), "amount": Decimal("1.20"), "bytes": b"raw"}]
    with OriginalJournal.create(
        tmp_path / "nested", budget=EvidenceBudget(), index_bytes=512 * 1024
    ) as owner:
        parent = owner.begin("cleanup", {})
        inner_writer = owner.begin_collection(parent, "inner")
        inner_writer.append(raw_values[0])
        inner = inner_writer.complete()
        outer_writer = owner.begin_collection(parent, "outer")
        outer_writer.append({"a": inner, "b": inner})
        leaf_work = owner.budget.workspace_work_bytes
        outer = outer_writer.complete()
        # Rows with collection edges stay on the original monotonic decoder.
        assert owner.budget.workspace_work_bytes == leaf_work
        converted = plain(outer)
        value = converted[0]
        assert type(value["a"]) is PlainCollectionRows
        assert value["a"] is not value["b"]
        assert list(value["a"]) == plain(raw_values)
        assert list(value["b"]) == plain(raw_values)
        assert type(outer[0]["a"]) is CollectionRows
        assert list(outer[0]["a"]) == raw_values
        assert canonical_original_digest(
            converted, owner=owner, budget=owner.budget
        ) == canonical_digest([{"a": plain(raw_values), "b": plain(raw_values)}])
        with OriginalJournal.create(
            tmp_path / "foreign", budget=EvidenceBudget(), index_bytes=512 * 1024
        ) as foreign:
            parent2 = foreign.begin("cleanup", {})
            empty = foreign.begin_collection(parent2, "empty").complete()
            with pytest.raises(ValueError, match="owner"):
                plain_graph({"nested": empty}, owner=owner, budget=owner.budget)
    with pytest.raises(ValueError, match="closed"):
        _ = value["a"][0]
