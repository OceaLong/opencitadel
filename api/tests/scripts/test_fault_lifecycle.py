"""Private coordination contract only; no browser, container or physical effects."""

import time
from uuid import uuid4

import pytest
from scripts.acceptance.fault_lifecycle import Lifecycle, LifecycleError


def test_contention_only_successful_exact_disarm_releases(tmp_path):
    gate = Lifecycle(tmp_path / "gate", {"invocation": "owned"}, alive=lambda pid: True)
    first, second = str(uuid4()), str(uuid4())
    assert gate.acquire(first, 1) == {"acquired": True}
    assert gate.acquire(second, 2) == {"busy": True}
    fault = str(uuid4())
    gate.bind_fault(first, fault, receipt_time_ns=time.time_ns())
    with pytest.raises(LifecycleError):
        gate.release(second, {})
    with pytest.raises(LifecycleError):
        gate.release(first, {"fault_id": fault, "cleanup": "control removed", "journal": []})
    gate.release(
        first,
        {
            "fault_id": fault,
            "cleanup": "control removed",
            "journal": [{"event": "disarm", "fault_id": fault, "complete": True}],
        },
    )
    assert gate.acquire(second, 2) == {"acquired": True}


@pytest.mark.parametrize("unsafe", ["failure", "dead", "foreign", "cancel"])
def test_failed_dead_foreign_or_cancelled_owner_retains_gate(tmp_path, unsafe):
    binding = {"invocation": "owned"}
    gate = Lifecycle(tmp_path / "gate", binding, alive=lambda pid: unsafe != "dead")
    first = str(uuid4())
    gate.acquire(first, 1)
    if unsafe in {"failure", "cancel"}:
        gate.fail(first)
    contender = Lifecycle(
        tmp_path / "gate",
        {"invocation": "foreign"} if unsafe == "foreign" else binding,
        alive=lambda pid: unsafe != "dead",
    )
    with pytest.raises(LifecycleError):
        contender.acquire(str(uuid4()), 2)
    assert gate.control.read("owner.json") is not None


def test_unsafe_path_is_not_followed(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(LifecycleError):
        Lifecycle(alias / "gate", {}, alive=lambda pid: True)


def contend(root, ready, results):
    ready.wait()
    gate = Lifecycle(root, {"invocation": "owned"}, alive=lambda pid: True)
    results.put(gate.acquire(str(uuid4()), 1))


def test_processes_atomically_choose_one_owner(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("fork")
    ready, results = context.Event(), context.Queue()
    root = tmp_path / "gate"
    workers = [context.Process(target=contend, args=(root, ready, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    ready.set()
    try:
        values = [results.get(timeout=5) for _ in workers]
        assert values.count({"acquired": True}) == 1
        assert values.count({"busy": True}) == 1
    finally:
        for worker in workers:
            worker.join(5)
            if worker.is_alive():
                worker.terminate()
                worker.join()
        results.close()
    assert all(worker.exitcode == 0 for worker in workers)


def test_stale_arm_and_unmatched_disarm_cannot_unlock(tmp_path):
    gate = Lifecycle(tmp_path / "gate", {}, alive=lambda pid: True)
    token, fault = str(uuid4()), str(uuid4())
    gate.acquire(token, 1)
    with pytest.raises(LifecycleError):
        gate.bind_fault(token, fault, receipt_time_ns=0)
    gate.bind_fault(token, fault, receipt_time_ns=time.time_ns())
    with pytest.raises(LifecycleError):
        gate.bind_fault(token, fault, receipt_time_ns=time.time_ns())
    with pytest.raises(LifecycleError):
        gate.release(token, {"fault_id": str(uuid4()), "cleanup": "control removed", "journal": []})
    assert gate.control.read("owner.json")["fault_id"] == fault
