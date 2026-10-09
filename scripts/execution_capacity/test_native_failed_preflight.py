"""Pure-file finite preflight tests; no failed reader or copy authority."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from scripts.execution_capacity import native_failed_preflight
from scripts.execution_capacity.attempt import AttemptLedger
from scripts.execution_capacity.native_failed_close import close_failed_host
from scripts.execution_capacity.native_failed_preflight import (
    DRAIN_BYTES,
    JOURNAL_BYTES,
    JOURNAL_ROWS,
    PLAN_BYTES,
    FixtureDiagnosticBudget,
    RunnerFailedOriginMap,
    preflight_failed_diagnostic,
)
from scripts.execution_capacity.test_native_failed_close import _prepared


def _fixture(tmp_path, monkeypatch, *, content=b"", acknowledged=0):
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    with AttemptLedger.create(parent, {"attempt_id": "parent"}) as ledger:
        ledger.append("parent-note", {"kind": "source-fixture"})
    key, plan, _raw = _prepared(child, monkeypatch, content=content, acknowledged=acknowledged)
    host = close_failed_host(child, plan, key)
    origins = RunnerFailedOriginMap(
        parent_origin=parent,
        parent_retained=parent,
        child_origin=child,
        child_retained=child,
    )
    return origins, key, host, target


def _preflight(origins, key, host, target, *, budget=None):
    return preflight_failed_diagnostic(
        origins,
        key,
        fresh_host=host,
        target_parent=target,
        budget=FixtureDiagnosticBudget() if budget is None else budget,
    )


@pytest.mark.parametrize(("content", "acknowledged"), [(b"", 0), (b"a" * 49152 + b"tail", 49152)])
def test_finite_failed_preflight_zero_and_small_partial(
    tmp_path, monkeypatch, content, acknowledged
):
    origins, key, host, target = _fixture(
        tmp_path, monkeypatch, content=content, acknowledged=acknowledged
    )
    budget = FixtureDiagnosticBudget()
    observed = _preflight(origins, key, host, target, budget=budget)
    assert observed.state == "preflight-only"
    assert observed.origins == origins
    assert observed.command_sha256 == key
    assert observed.close_row_digest == host.close_row_digest
    assert observed.parent_journal_rows == 1
    assert observed.child_journal_rows >= 4
    assert observed.target_reserved_bytes == observed.source_bytes > 0
    assert budget.source_reserved == budget.target_reserved == observed.source_bytes
    assert budget.work_reserved == observed.work_reserved_bytes
    assert len(observed.members) == 8
    assert list(target.iterdir()) == []


@pytest.mark.parametrize(
    ("member", "limit"), [("plan.json", PLAN_BYTES), ("attempt.jsonl", JOURNAL_BYTES)]
)
def test_finite_failed_preflight_rejects_oversize_companion_before_parse(
    tmp_path, monkeypatch, member, limit
):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    with (origins.parent_retained / member).open("ab") as stream:
        stream.write(b"x" * (limit + 1))
    monkeypatch.setattr(
        native_failed_preflight,
        "strict_json",
        lambda *_: pytest.fail("manifest parsed before companion size preflight"),
    )
    budget = FixtureDiagnosticBudget()
    with pytest.raises(ValueError, match="fixture bound"):
        _preflight(origins, key, host, target, budget=budget)
    assert budget.source_reserved == budget.target_reserved == 0


@pytest.mark.parametrize("quota", ["source", "target", "combined", "work"])
def test_finite_failed_preflight_rejects_individual_and_cumulative_quota(
    tmp_path, monkeypatch, quota
):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    values = {
        "source_limit": 1 if quota == "source" else native_failed_preflight.SOURCE_BYTES,
        "target_limit": 1 if quota == "target" else native_failed_preflight.TARGET_BYTES,
        "combined_limit": 1 if quota == "combined" else native_failed_preflight.COMBINED_BYTES,
        "work_limit": 1 if quota == "work" else native_failed_preflight.WORK_BYTES,
    }
    budget = FixtureDiagnosticBudget(**values)
    with pytest.raises(ValueError, match="quota exceeded"):
        _preflight(origins, key, host, target, budget=budget)
    assert budget.source_reserved == budget.target_reserved == budget.work_reserved == 0


def test_finite_failed_preflight_reservations_are_cumulative(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    baseline = _preflight(origins, key, host, target)
    budget = FixtureDiagnosticBudget(
        source_limit=baseline.source_bytes,
        target_limit=baseline.target_reserved_bytes,
    )
    _preflight(origins, key, host, target, budget=budget)
    with pytest.raises(ValueError, match="quota exceeded"):
        _preflight(origins, key, host, target, budget=budget)
    assert budget.source_reserved == baseline.source_bytes
    assert budget.target_reserved == baseline.target_reserved_bytes


def test_finite_failed_preflight_rejects_native_member_above_fixture_cap(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    path = origins.child_retained / f"native-{key}" / "failure-drain.ndjson"
    with path.open("ab") as stream:
        stream.write(b"x" * (DRAIN_BYTES + 1))
    with pytest.raises(ValueError, match="fixture bound"):
        _preflight(origins, key, host, target)


def test_finite_failed_preflight_rejects_excess_journal_rows_without_parse(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    with (origins.parent_retained / "attempt.jsonl").open("ab") as stream:
        stream.write(b"{}\n" * (JOURNAL_ROWS + 1))
    with pytest.raises(ValueError, match="row count"):
        _preflight(origins, key, host, target)


def test_finite_failed_preflight_cannot_raise_fixture_ceiling():
    with pytest.raises(ValueError, match="fixed finite"):
        FixtureDiagnosticBudget(target_limit=native_failed_preflight.TARGET_BYTES + 1)


def test_finite_failed_preflight_requires_explicit_runner_inputs(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="runner-owned"):
        _preflight(None, key, host, target)
    with pytest.raises(ValueError, match="runner-owned"):
        _preflight(origins, "0" * 64, host, target)
    with pytest.raises(ValueError, match="runner-owned"):
        preflight_failed_diagnostic(
            origins, key, fresh_host=None, target_parent=target, budget=FixtureDiagnosticBudget()
        )
    aliased = RunnerFailedOriginMap(
        origins.parent_origin,
        origins.parent_retained,
        origins.parent_origin,
        origins.child_retained,
    )
    with pytest.raises(ValueError, match="distinct parent/child"):
        _preflight(aliased, key, host, target)


@pytest.mark.parametrize("defect", ["extra", "symlink", "record-symlink", "target-symlink"])
def test_finite_failed_preflight_rejects_foreign_members_or_symlinks(tmp_path, monkeypatch, defect):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    native = origins.child_retained / f"native-{key}"
    if defect == "extra":
        (native / "extra.bin").write_bytes(b"x")
    elif defect == "symlink":
        (native / "failure.json").unlink()
        (native / "failure.json").symlink_to(native / "command.json")
    elif defect == "record-symlink":
        (native / "records" / "extra-link").symlink_to(native / "command.json")
    else:
        link = tmp_path / "target-link"
        link.symlink_to(target, target_is_directory=True)
        target = link
    with pytest.raises((ValueError, OSError)):
        _preflight(origins, key, host, target)


def test_finite_failed_preflight_rejects_target_disk_shortage(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    original = native_failed_preflight.os.fstatvfs

    def no_space(fd):
        info = original(fd)
        return type("NoSpace", (), {"f_bavail": 0, "f_frsize": info.f_frsize})()

    monkeypatch.setattr(native_failed_preflight.os, "fstatvfs", no_space)
    with pytest.raises(ValueError, match="target disk space"):
        _preflight(origins, key, host, target)


def test_finite_failed_preflight_target_space_checks_cumulative_reservation(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    expected = _preflight(origins, key, host, target).target_reserved_bytes
    available = expected + native_failed_preflight.TARGET_HEADROOM_BYTES + 1
    assert available < 2 * expected + native_failed_preflight.TARGET_HEADROOM_BYTES
    original = native_failed_preflight.os.fstatvfs

    def limited_space(fd):
        original(fd)
        return type("LimitedSpace", (), {"f_bavail": available, "f_frsize": 1})()

    monkeypatch.setattr(native_failed_preflight.os, "fstatvfs", limited_space)
    budget = FixtureDiagnosticBudget()
    _preflight(origins, key, host, target, budget=budget)
    with pytest.raises(ValueError, match="cumulative target disk space"):
        _preflight(origins, key, host, target, budget=budget)
    assert budget.target_reserved == expected


def test_finite_failed_preflight_target_space_reservation_is_serialized(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    expected = _preflight(origins, key, host, target).target_reserved_bytes
    available = expected + native_failed_preflight.TARGET_HEADROOM_BYTES + 1
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def paused_space(_fd):
        nonlocal calls
        with calls_lock:
            calls += 1
            first = calls == 1
        if first:
            entered.set()
            assert release.wait(5)
        return type("LimitedSpace", (), {"f_bavail": available, "f_frsize": 1})()

    monkeypatch.setattr(native_failed_preflight.os, "fstatvfs", paused_space)
    budget = FixtureDiagnosticBudget()

    def second():
        second_started.set()
        return _preflight(origins, key, host, target, budget=budget)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(_preflight, origins, key, host, target, budget=budget)
        assert entered.wait(5)
        second_result = pool.submit(second)
        assert second_started.wait(5)
        release.set()
        assert first_result.result(timeout=5).state == "preflight-only"
        with pytest.raises(ValueError, match="cumulative target disk space"):
            second_result.result(timeout=5)
    assert calls == 2
    assert budget.target_reserved == expected


@pytest.mark.parametrize("location", ["child", "records", "ancestor"])
def test_finite_failed_preflight_rejects_nested_or_ancestor_target(tmp_path, monkeypatch, location):
    origins, key, host, _target = _fixture(tmp_path, monkeypatch)
    target = {
        "child": origins.child_retained,
        "records": origins.child_retained / f"native-{key}" / "records",
        "ancestor": tmp_path,
    }[location]
    with pytest.raises(ValueError, match="ancestry overlaps"):
        _preflight(origins, key, host, target)


def test_finite_failed_preflight_rejects_huge_child_journal_before_parse(tmp_path, monkeypatch):
    origins, key, host, target = _fixture(tmp_path, monkeypatch)
    with (origins.child_retained / "attempt.jsonl").open("ab") as stream:
        stream.write(b" " * (JOURNAL_BYTES + 1))
    monkeypatch.setattr(
        native_failed_preflight,
        "strict_json",
        lambda *_: pytest.fail("manifest parsed before huge journal rejection"),
    )
    with pytest.raises(ValueError, match="fixture bound"):
        _preflight(origins, key, host, target)
