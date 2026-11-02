"""Closed trusted context configuration; no services or runtime collectors."""

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget


def test_missing_original_secret_and_ambiguous_locations_fail_without_io(tmp_path):
    from scripts.execution_capacity.offline_context import BaseLocation, OfflineProofContext

    location = BaseLocation(tmp_path / "original", tmp_path / "retained", tmp_path / "image")
    for bases, signing, cursor in (
        ([location], "", b"cursor"),
        ([location], "sign", b""),
        ([location, location], "sign", b"cursor"),
    ):
        with pytest.raises(ValueError, match=r".+"):
            OfflineProofContext(
                bases=bases,
                rounds=[],
                signing_secret=signing,
                cursor_secret=cursor,
                budget=EvidenceBudget(),
            )
    with pytest.raises(TypeError):
        OfflineProofContext(
            bases=[{"origin": tmp_path}],
            rounds=[],
            signing_secret="sign",
            cursor_secret=b"cursor",
            budget=EvidenceBudget(),
        )


def test_missing_original_bytes_never_grant_authority(tmp_path):
    from scripts.execution_capacity.offline_context import (
        BaseLocation,
        OfflineProofContext,
        PrivateProofError,
    )

    ctx = OfflineProofContext(
        bases=[
            BaseLocation(
                tmp_path / "secret-origin", tmp_path / "secret-retained", tmp_path / "secret-image"
            )
        ],
        rounds=[],
        signing_secret="secret-material",
        cursor_secret=b"secret-cursor",
        budget=EvidenceBudget(),
    )
    with pytest.raises(PrivateProofError) as error:
        ctx.project()
    assert "secret" not in str(error.value)
    assert str(tmp_path) not in repr(ctx)


def test_all_facade_entrypoints_require_concrete_context_before_safe_only_input(tmp_path):
    from scripts.acceptance.capacity import (
        derive_capacity_report,
        prepare_capacity_evidence,
        validate_capacity_report,
    )
    from scripts.execution_capacity.offline_context import PrivateProofError

    with pytest.raises(PrivateProofError):
        derive_capacity_report(
            artifacts=[],
            binding={},
            completed_binding={},
            root=tmp_path,
            started_at="",
            finished_at="",
        )
    assert validate_capacity_report({}, {}, tmp_path) == ["private C2c original context required"]
    receipt = prepare_capacity_evidence(
        report_path=None,
        fixture_path=None,
        evidence_root=tmp_path,
        build={},
        run_id="fixture",
        project="fixture",
    )
    assert receipt["errors"] == ["capacity handoff: private C2c original context required"]
    assert "percentiles_ms" not in receipt


@pytest.mark.parametrize("fault", [None, "body", "missing", "order", "source"])
def test_repeated_original_origin_consumes_fresh_complete_bounded_members(tmp_path, fault):
    from scripts.execution_capacity.offline_context import _record_fingerprint

    origin, source = tmp_path / "origin", tmp_path / "source"
    first = [
        ("plan.json", {"sha256": "a" * 64, "size_bytes": 10}),
        ("attempt.jsonl", {"sha256": "b" * 64, "size_bytes": 20}),
    ]
    fingerprints = {}
    closed = []

    def members(rows):
        try:
            yield from rows
        finally:
            closed.append(True)

    _record_fingerprint(fingerprints, origin, source, members(first))
    changed = list(first)
    if fault == "body":
        changed[1] = ("attempt.jsonl", {"sha256": "c" * 64, "size_bytes": 20})
    elif fault == "missing":
        changed.pop()
    elif fault == "order":
        changed.reverse()
    second_source = tmp_path / "other" if fault == "source" else source
    if fault is None:
        _record_fingerprint(fingerprints, origin, second_source, members(changed))
    else:
        with pytest.raises(ValueError, match="original member changed between rounds"):
            _record_fingerprint(fingerprints, origin, second_source, members(changed))
    assert len(closed) == 2
    assert len(fingerprints) == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_fingerprint_after_one_member_promotes_retained_failure_traceback(tmp_path, cancel):
    import asyncio

    from scripts.execution_capacity.offline_context import _record_fingerprint_budgeted

    parent = EvidenceBudget(bytes_limit=1024 * 1024, rows_limit=100)
    child = parent.child()
    closed = []

    def members():
        try:
            yield "plan.json", {"sha256": "a" * 64, "size_bytes": 10}
            if cancel:
                raise asyncio.CancelledError()
            raise ValueError("late member failed")
        finally:
            closed.append(True)

    error_type = asyncio.CancelledError if cancel else ValueError
    with pytest.raises(error_type) as error:
        _record_fingerprint_budgeted({}, tmp_path / "origin", tmp_path / "source", members(), child)
    assert closed == [True]
    assert (child.bytes, child.workspace_bytes, child.workspace_work_bytes) == (32_768, 0, 32_768)
    assert (parent.bytes, parent.workspace_bytes, parent.workspace_work_bytes) == (
        32_768,
        0,
        32_768,
    )
    fingerprint_frame = None
    traceback = error.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name == "_record_fingerprint":
            fingerprint_frame = traceback.tb_frame
        traceback = traceback.tb_next
    assert fingerprint_frame is not None
    assert fingerprint_frame.f_locals["raw"].startswith(b'["plan.json",')


def test_original_location_and_member_encoding_have_fixed_upper_bounds(tmp_path):
    from scripts.execution_capacity.offline_context import (
        MAX_ORIGINAL_PATH_BYTES,
        _absolute,
        _member_bytes,
    )

    with pytest.raises(ValueError, match="bounded original location"):
        _absolute(tmp_path / ("x" * MAX_ORIGINAL_PATH_BYTES))
    receipt = {"sha256": "a" * 64, "size_bytes": (1 << 63) - 1}
    raw = _member_bytes("😀" * 1024, receipt)
    assert len(raw) < 13 * 1024 < 32_768
    with pytest.raises(ValueError, match="closed original file receipt"):
        _member_bytes("x", {**receipt, "size_bytes": 1 << 63})
