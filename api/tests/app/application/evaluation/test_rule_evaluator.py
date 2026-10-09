import asyncio
import json

import pytest

from app.domain.evaluation.rule_engine import RuleEvidence
from app.domain.evaluation.rule_validation import validate_rule_definition

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": "^(a+)+$"},
        {"type": "object", "patternProperties": {"^(a+)+$": {"type": "string"}}},
    ],
)
async def test_accepted_regex_is_killed_and_later_work_progresses(schema, monkeypatch):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator

    processes = []
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    rule = {"kind": "json_schema", "schema": schema}
    validate_rule_definition(rule)
    subject = "a" * 32 + "!"
    if schema["type"] == "object":
        subject = {subject: "x"}
    evaluator = IsolatedRuleEvaluator(timeout_seconds=0.75)
    turns = 0

    async def heartbeat():
        nonlocal turns
        while True:
            turns += 1
            await asyncio.sleep(0.02)

    tick = asyncio.create_task(heartbeat())
    try:
        score = await asyncio.wait_for(
            evaluator.evaluate(rule, json.dumps(subject), None, RuleEvidence()), 3
        )
    finally:
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
    assert score.status == "error"
    assert score.value is None
    assert score.reason == "rule_evaluation_timeout"
    assert turns >= 3
    good = await evaluator.evaluate(
        {"kind": "text_exact", "expected": "ok"}, "ok", None, RuleEvidence()
    )
    assert good.status == "valid"
    assert good.value is True
    assert len(processes) == 2
    assert all(p.returncode is not None for p in processes)


async def test_cancellation_kills_and_reaps_running_rule(monkeypatch):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator

    processes = []
    spawned = asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    evaluator = IsolatedRuleEvaluator(timeout_seconds=5)
    pending = asyncio.create_task(
        evaluator.evaluate(
            {"kind": "json_schema", "schema": {"pattern": "^(a+)+$"}},
            json.dumps("a" * 40 + "!"),
            None,
            RuleEvidence(),
        )
    )
    await asyncio.wait_for(spawned.wait(), 2)
    await asyncio.sleep(0.3)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 2)
    assert processes[0].returncode is not None


async def test_artifact_schema_uses_same_killable_boundary():
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator
    from app.domain.evaluation.rule_engine import ArtifactEvidence
    from app.domain.models.resource_pin import ResourceIdentity

    rule = {
        "kind": "artifact",
        "artifact_kind": "doc",
        "schema": {"type": "string", "pattern": "^(a+)+$"},
    }
    validate_rule_definition(rule)
    evidence = RuleEvidence(
        artifacts=(
            ArtifactEvidence(
                ResourceIdentity(
                    resource_kind="artifact", resource_id="artifact", resource_version="1"
                ),
                "doc",
                "a" * 40 + "!",
            ),
        )
    )
    result = await IsolatedRuleEvaluator(timeout_seconds=0.75).evaluate(
        rule, "answer", None, evidence
    )
    assert result.status == "error"
    assert result.value is None
    assert result.reason == "rule_evaluation_timeout"


async def test_cancellation_during_spawn_reaps_eventual_child(monkeypatch):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator

    processes = []
    spawning = asyncio.Event()
    release = asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def delayed(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        spawning.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    pending = asyncio.create_task(
        IsolatedRuleEvaluator().evaluate(
            {"kind": "text_exact", "expected": "ok"}, "ok", None, RuleEvidence()
        )
    )
    await asyncio.wait_for(spawning.wait(), 2)
    pending.cancel()
    await asyncio.sleep(0.02)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 2)
    assert processes[0].returncode is not None


@pytest.fixture
def held_rule_cleanup(monkeypatch):
    """Hold the real child's second communicate at the timeout-cleanup boundary."""
    processes = []
    cleanup_entered = asyncio.Event()
    release = asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        communicate = process.communicate
        calls = 0

        async def gated(input=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                cleanup_entered.set()
                await release.wait()
            return await communicate(input)

        monkeypatch.setattr(process, "communicate", gated)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    return processes, cleanup_entered, release


@pytest.mark.parametrize("cancel_count", [0, 1, 3])
async def test_timeout_cleanup_preserves_cancellation_after_reaping(
    held_rule_cleanup, cancel_count
):
    from app.application.evaluation.rule_evaluator import IsolatedRuleEvaluator

    processes, cleanup_entered, release = held_rule_cleanup
    pending = asyncio.create_task(
        IsolatedRuleEvaluator(timeout_seconds=0.1).evaluate(
            {"kind": "json_schema", "schema": {"pattern": "^(a+)+$"}},
            json.dumps("a" * 40 + "!"),
            None,
            RuleEvidence(),
        )
    )
    try:
        await asyncio.wait_for(cleanup_entered.wait(), 3)
        for _ in range(cancel_count):
            pending.cancel()
            await asyncio.sleep(0)
        assert not pending.done()
    finally:
        release.set()
    if cancel_count:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 2)
    else:
        score = await asyncio.wait_for(pending, 2)
        assert score.status == "error"
        assert score.value is None
        assert score.reason == "rule_evaluation_timeout"
    assert len(processes) == 1
    assert processes[0].returncode is not None
