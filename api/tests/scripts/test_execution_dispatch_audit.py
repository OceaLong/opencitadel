"""Synthetic observer/validator unit checks, never real replay evidence."""

import asyncio
from types import SimpleNamespace

import pytest
from scripts.acceptance.dispatch_audit import AuditLog, observe, validate_window


def context():
    return SimpleNamespace(
        run=SimpleNamespace(run_id="run-a"),
        activity_id="activity-a",
        generation=0,
        claim_generation=1,
        owner_user_id="owner",
        team_id=None,
    )


def test_observer_preserves_values_and_private_payloads_are_absent(tmp_path):
    log = AuditLog(
        tmp_path / "audit",
        {"project": "project", "run_id": "invocation", "source_sha256": "a" * 64},
    )
    secret = object()

    async def original(self, payload, ctx, *, name, arguments):
        assert payload is secret
        assert arguments is secret
        return secret

    wrapped = observe(original, "catalog", log)
    assert (
        asyncio.run(wrapped(None, secret, context(), name="shell_execute", arguments=secret))
        is secret
    )
    records = log.records()
    assert [row["event"] for row in records] == ["boot", "begin", "end"]
    assert records[-1]["outcome"] == "returned"
    assert "arguments" not in str(records)
    assert "payload" not in str(records)


def test_cancellation_propagates_and_closes_interval(tmp_path):
    log = AuditLog(tmp_path / "audit", {})

    async def original(*args, **kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            observe(original, "handler", log)(
                None, SimpleNamespace(activity_id="activity-a"), context()
            )
        )
    assert log.records()[-1]["outcome"] == "CancelledError"


def test_zero_catalog_requires_positive_controls_complete_replay_and_contiguous_log(tmp_path):
    binding = {"project": "project", "run_id": "invocation", "source_sha256": "a" * 64}
    log = AuditLog(tmp_path / "audit", binding)

    async def noop(*args, **kwargs):
        return None

    async def run():
        ctx = context()

        async def positive(*args, **kwargs):
            await observe(noop, "catalog", log)(None, None, ctx)

        await observe(positive, "handler", log)(None, None, ctx)
        replay = SimpleNamespace(**{**vars(ctx), "run": SimpleNamespace(run_id="replay")})

        async def simulated(*args, **kwargs):
            await observe(noop, "replay", log)(None, None, replay)

        await observe(simulated, "handler", log)(None, None, replay)

    asyncio.run(run())
    rows = log.records()
    result = validate_window(
        rows, binding, replay_run="replay", positive_runs=["run-a"], owner_user_id="owner"
    )
    assert result["catalog_entries"] == 0
    assert result["replay_entries"] == 1
    for invalid in (
        rows[:-1],
        rows[:2] + rows[3:],
        [*rows, rows[-1]],
        [{**rows[0], "boot_id": "other"}, *rows[1:]],
    ):
        with pytest.raises(ValueError, match=r"audit|interval|replay|control"):
            validate_window(
                invalid,
                binding,
                replay_run="replay",
                positive_runs=["run-a"],
                owner_user_id="owner",
            )
    with pytest.raises(ValueError, match=r"audit|interval|replay|control"):
        validate_window(
            rows, binding, replay_run="absent", positive_runs=["run-a"], owner_user_id="owner"
        )
    with pytest.raises(ValueError, match=r"audit|interval|replay|control"):
        validate_window(
            rows, binding, replay_run="replay", positive_runs=["absent"], owner_user_id="owner"
        )
    with pytest.raises(ValueError, match=r"audit|interval|replay|control"):
        validate_window(
            rows, binding, replay_run="replay", positive_runs=["run-a"], owner_user_id="foreign"
        )


def test_log_failure_prevents_unobserved_dispatch(tmp_path):
    log = AuditLog(tmp_path / "audit", {})
    log.stream.close()
    calls = []

    async def original(*args, **kwargs):
        calls.append(True)

    with pytest.raises(ValueError, match="closed"):
        asyncio.run(observe(original, "handler", log)(None, None, context()))
    assert calls == []


def test_detached_replay_interval_does_not_prove_handler_observed_it(tmp_path):
    log = AuditLog(tmp_path / "audit", {})

    async def noop(*args, **kwargs):
        return None

    async def run():
        for identity, kinds in (
            ("run-a", ("handler", "catalog")),
            ("replay", ("handler", "replay")),
        ):
            ctx = context()
            ctx.run = SimpleNamespace(run_id=identity)
            for kind in kinds:
                await observe(noop, kind, log)(None, None, ctx)

    asyncio.run(run())
    with pytest.raises(ValueError, match="handler"):
        validate_window(
            log.records(), {}, replay_run="replay", positive_runs=["run-a"], owner_user_id="owner"
        )


@pytest.mark.parametrize(
    "reason", ["slot_unmatched", "arguments_mismatch", "recorded_object_missing"]
)
def test_replay_observer_preserves_allowlisted_reason_without_private_exception_text(
    tmp_path, reason
):
    from app.domain.evaluation.errors import ReplayMismatch

    log = AuditLog(tmp_path / "audit", {})

    async def original(*args, **kwargs):
        raise ReplayMismatch(reason)

    with pytest.raises(ReplayMismatch):
        asyncio.run(observe(original, "replay", log)(None, None, context()))
    assert log.records()[-1]["mismatch_reason"] == reason


def test_replay_observer_never_logs_arbitrary_reason(tmp_path):
    from app.domain.evaluation.errors import ReplayMismatch

    log = AuditLog(tmp_path / "audit", {})

    async def original(*args, **kwargs):
        raise ReplayMismatch("private-secret-path")

    with pytest.raises(ReplayMismatch):
        asyncio.run(observe(original, "replay", log)(None, None, context()))
    assert "private-secret-path" not in str(log.records())
