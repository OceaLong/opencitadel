"""Receipt producers never promote unfinished queries or consume foreign originals."""

import pytest
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.query_outputs import query_output


@pytest.mark.parametrize("fault", [None, "missing", "extra", "late", "order"])
def test_query_receipts_compare_complete_original_sequence(tmp_path, fault):
    with EvidenceOwner(original_root=tmp_path / "originals", index_bytes=512 * 1024) as evidence:
        with pytest.raises(ValueError, match="query output"):
            query_output(evidence.journal, parent=None, slot="reads")
        evidence.begin_cleanup()
        out = query_output(evidence.journal, parent=evidence._cleanup_token, slot="reads")
        rows = [{"name": "first", "end_ns": 2}, {"name": "second", "error": "OSError", "end_ns": 4}]
        out.extend(rows)
        expected = out.complete()
        actual = query_output(evidence.journal, expected=expected)
        if fault == "order":
            with pytest.raises(ValueError, match="output row differs"):
                actual.append(rows[1])
            return
        actual.append(rows[0])
        if fault == "missing":
            with pytest.raises(ValueError, match="output count differs"):
                actual.complete()
            return
        if fault == "late":
            with pytest.raises(ValueError, match="output row differs"):
                actual.append({**rows[1], "end_ns": 5})
            return
        actual.append(rows[1])
        if fault == "extra":
            with pytest.raises(ValueError, match="output row differs"):
                actual.append(rows[1])
            return
        assert actual.complete() is expected


def test_empty_receipts_finish_and_reject_foreign_even_when_empty(tmp_path):
    with (
        EvidenceOwner(original_root=tmp_path / "left", index_bytes=512 * 1024) as left,
        EvidenceOwner(original_root=tmp_path / "right", index_bytes=512 * 1024) as right,
    ):
        left.begin_cleanup()
        output = query_output(left.journal, parent=left._cleanup_token, slot="empty")
        expected = output.complete()
        assert not expected
        assert query_output(left.journal, expected=expected).complete() is expected
        with pytest.raises(ValueError, match="original query output"):
            query_output(right.journal, expected=expected)
        with pytest.raises(ValueError, match="original query output"):
            query_output(left.journal, expected=[])


@pytest.mark.asyncio
async def test_inventory_query_finish_rejects_cancelled_and_inflight_outputs():
    import asyncio

    from scripts.execution_capacity.inventory_sql import InventoryQueries

    class DB:
        async def execute(self, *args):
            with pytest.raises(ValueError, match="in flight"):
                query.finish_outputs()
            raise asyncio.CancelledError()

    query = InventoryQueries(DB())
    with pytest.raises(asyncio.CancelledError):
        await query.rows("cancelled", "SELECT 1")
    with pytest.raises(ValueError, match="aborted"):
        query.finish_outputs()
    assert query.reads == []


@pytest.mark.asyncio
async def test_inventory_query_empty_finish_forbids_later_queries():
    from scripts.execution_capacity.inventory_sql import InventoryQueries

    query = InventoryQueries(object())
    assert query.finish_outputs() == []
    with pytest.raises(ValueError, match="not accepting"):
        await query.rows("late", "SELECT 1")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "stream"])
async def test_cancelled_snapshot_preserves_cause_and_rolls_back(monkeypatch, phase):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from scripts.execution_capacity import inventory_sql

    calls = []

    class DB:
        async def rollback(self):
            calls.append("rollback")
            if len(calls) == 2:
                raise OSError("fixture rollback failure")

        async def connection(self, **kwargs):
            pass

        async def execute(self, *args):
            if phase == "preflight":
                raise asyncio.CancelledError()
            return SimpleNamespace(
                mappings=lambda: SimpleNamespace(
                    one=lambda: {
                        "row_count": 1,
                        "max_bytes": 1,
                        "total_bytes": 1,
                        "read_only": "on",
                        "isolation": "repeatable read",
                        "snapshot": "one",
                    }
                )
            )

        async def stream(self, *args):
            raise asyncio.CancelledError()

    @asynccontextmanager
    async def sessions():
        yield DB()

    async def configure(*args):
        pass

    monkeypatch.setattr(inventory_sql, "configure_session_authorization", configure)
    with pytest.raises(BaseExceptionGroup) as caught:
        async with inventory_sql.read_snapshot(sessions, None) as query:
            await query.rows("cancelled", "SELECT 1")
    assert [type(e) for e in caught.value.exceptions] == [
        asyncio.CancelledError,
        ValueError,
        OSError,
    ]
    assert calls == ["rollback", "rollback"]
    assert query.reads == []
