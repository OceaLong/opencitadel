"""Exact small accounting oracle for one explicitly scoped original member digest."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError


def test_persistent_active_peak_and_cumulative_workspace_remain_separate():
    root = EvidenceBudget(bytes_limit=100, rows_limit=20, work_bytes_limit=80, work_rows_limit=10)
    child = root.child(bytes_limit=80, work_bytes_limit=60)
    child.reserve(10, rows=1)
    outer = root.reserve_workspace(20, rows=2)
    inner = child.reserve_workspace(30, rows=3)
    assert (
        root.bytes,
        root.workspace_bytes,
        root.workspace_peak_bytes,
        root.workspace_work_bytes,
    ) == (10, 50, 50, 50)
    assert (root.rows, root.workspace_rows, root.workspace_work_rows) == (1, 5, 5)
    with pytest.raises(EvidenceQuotaError):
        root.reserve(41, rows=0)
    root.reserve(40, rows=0)
    inner.release()
    outer.release()
    assert (
        root.bytes,
        root.workspace_bytes,
        root.workspace_peak_bytes,
        root.workspace_work_bytes,
    ) == (50, 0, 50, 50)
    again = child.reserve_workspace(30)
    again.release()
    assert root.workspace_work_bytes == 80
    with pytest.raises(EvidenceQuotaError, match="cumulative workspace"):
        child.reserve_workspace(1)
    with pytest.raises(ValueError, match="already released"):
        again.release()


def test_concurrent_child_scopes_sum_at_parent_and_exception_keeps_persistent_charge():
    root = EvidenceBudget(bytes_limit=100, rows_limit=10, work_bytes_limit=100)
    left, right = root.child(), root.child()
    left.reserve(10)
    arrived = threading.Barrier(3)
    leave = threading.Barrier(3)

    def worker(child):
        lease = child.reserve_workspace(25)
        try:
            arrived.wait()
            leave.wait()
        finally:
            lease.release()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, child) for child in (left, right)]
        arrived.wait()
        assert (root.bytes, root.workspace_bytes, root.workspace_peak_bytes) == (10, 50, 50)
        with pytest.raises(EvidenceQuotaError):
            right.reserve(41, rows=0)
        leave.wait()
        for future in futures:
            future.result()
    assert (root.bytes, root.workspace_bytes, root.workspace_work_bytes) == (10, 0, 50)


def test_base_parent_child_and_worker_lifetimes_overlap_at_one_parent():
    root = EvidenceBudget(bytes_limit=100, rows_limit=10, work_bytes_limit=100)
    base, parent, child, worker = (root.child() for _ in range(4))
    base.reserve(10)
    entered = threading.Event()
    leaving = threading.Event()

    def worker_read():
        lease = worker.reserve_workspace(30)
        try:
            entered.set()
            assert leaving.wait(5)
        finally:
            lease.release()

    base_scope = base.reserve_workspace(20)
    parent_scope = parent.reserve_workspace(15)
    child_scope = child.reserve_workspace(25)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker_read)
        assert entered.wait(5)
        assert (root.bytes, root.workspace_bytes, root.workspace_peak_bytes) == (10, 90, 90)
        with pytest.raises(EvidenceQuotaError):
            root.reserve(1, rows=0)
        leaving.set()
        future.result()
    child_scope.release()
    parent_scope.release()
    base_scope.release()
    assert (root.bytes, root.workspace_bytes, root.workspace_work_bytes) == (10, 0, 90)


def test_returned_graph_and_suspended_generator_hold_scope_until_last_use_or_cancel():
    budget = EvidenceBudget(bytes_limit=100, rows_limit=10)
    budget.reserve(5)
    lease = budget.reserve_workspace(20)
    graph = {"items": [1, 2]}

    def notes():
        yield graph["items"][0]
        yield graph["items"][1]

    iterator = notes()
    assert next(iterator) == 1
    assert budget.workspace_bytes == 20
    assert next(iterator) == 2
    with pytest.raises(StopIteration):
        next(iterator)
    lease.release()
    assert (budget.bytes, budget.workspace_bytes, budget.workspace_work_bytes) == (5, 0, 20)

    async def cancelled():
        active = budget.reserve_workspace(20)
        try:
            raise asyncio.CancelledError
        finally:
            active.release()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    assert (budget.bytes, budget.workspace_bytes, budget.workspace_work_bytes) == (5, 0, 40)


def test_promote_moves_only_its_active_scope_to_permanent_at_every_ancestor():
    root = EvidenceBudget(bytes_limit=100, rows_limit=20, work_bytes_limit=100)
    child = root.child(bytes_limit=80, rows_limit=15)
    child.reserve(10, rows=1)
    sibling = root.reserve_workspace(15, rows=2)
    failed = child.reserve_workspace(25, rows=3)
    before = (root.bytes + root.workspace_bytes, root.rows + root.workspace_rows)
    assert before == (50, 6)
    failed.promote()
    assert (root.bytes, root.rows, root.workspace_bytes, root.workspace_rows) == (35, 4, 15, 2)
    assert (child.bytes, child.rows, child.workspace_bytes, child.workspace_rows) == (35, 4, 0, 0)
    assert (root.bytes + root.workspace_bytes, root.rows + root.workspace_rows) == before
    assert root.workspace_work_bytes == 40
    with pytest.raises(ValueError, match="already released or promoted"):
        failed.promote()
    with pytest.raises(ValueError, match="already released"):
        failed.release()
    sibling.release()
    assert (root.bytes, root.workspace_bytes, root.workspace_work_bytes) == (35, 0, 40)


def test_concurrent_promote_and_other_live_scope_preserve_parent_balance():
    root = EvidenceBudget(bytes_limit=100, rows_limit=10, work_bytes_limit=100)
    child = root.child()
    arrived, finish = threading.Event(), threading.Event()
    main_scope = root.reserve_workspace(20)

    def worker():
        failed = child.reserve_workspace(30)
        arrived.set()
        assert finish.wait(5)
        failed.promote()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        assert arrived.wait(5)
        assert (root.bytes, root.workspace_bytes) == (0, 50)
        finish.set()
        future.result()
    assert (root.bytes, root.workspace_bytes, root.workspace_peak_bytes) == (30, 20, 50)
    main_scope.release()
    assert (root.bytes, root.workspace_bytes, root.workspace_work_bytes) == (30, 0, 50)
