"""Killable runtime boundary for the pure evaluator, including schema regex work.

Every dynamic rule in the automatic consumer uses a fresh child interpreter.
No DB transaction is held; stdin/stdout carry bounded JSON, never executable text.
"""

import asyncio
import json
import sys
from contextlib import suppress
from dataclasses import asdict

from app.domain.evaluation.rule_engine import MISSING, ArtifactEvidence, RuleEvidence
from app.domain.evaluation.scoring import RecordingEvidence, ScoreValue
from app.domain.models.resource_pin import ResourceIdentity

MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
RULE_TIMEOUT_SECONDS = 2.0


def _encode(value, limit):
    def default(item):
        return item.model_dump(mode="json")

    parts = []
    length = 0
    for part in json.JSONEncoder(
        default=default, allow_nan=False, separators=(",", ":")
    ).iterencode(value):
        data = part.encode()
        length += len(data)
        if length > limit:
            raise ValueError("rule_payload_limit")
        parts.append(data)
    return b"".join(parts)


async def _kill_and_reap(process):
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    # Shield cleanup even from repeated cancellation: no abandoned compute or pipes.
    cleanup = asyncio.create_task(process.communicate())
    cancellation = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as error:
            cancellation = error
    cleanup.result()
    if cancellation is not None:
        raise cancellation


class IsolatedRuleEvaluator:
    def __init__(self, *, timeout_seconds=RULE_TIMEOUT_SECONDS):
        if not 0.05 <= timeout_seconds <= 10:
            raise ValueError("invalid_rule_deadline")
        self.timeout = timeout_seconds

    async def evaluate(self, rule, subject, reference, evidence):
        try:
            payload = _encode(
                {
                    "rule": rule,
                    "subject": None if subject is MISSING else subject,
                    "subject_available": subject is not MISSING,
                    "reference": reference,
                    "evidence": asdict(evidence),
                },
                MAX_INPUT_BYTES,
            )
        except (ValueError, TypeError, RecursionError):
            return ScoreValue(status="error", value=None, reason="rule_payload_limit")
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                __name__,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        try:
            process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Cancellation during spawn still owns the eventual child and must reap it.
            while not creation.done():
                try:
                    await asyncio.shield(creation)
                except asyncio.CancelledError:
                    continue
            await _kill_and_reap(creation.result())
            raise
        except OSError:
            return ScoreValue(status="error", value=None, reason="rule_worker_unavailable")
        try:
            output, _ = await asyncio.wait_for(process.communicate(payload), timeout=self.timeout)
        except TimeoutError:
            await _kill_and_reap(process)
            return ScoreValue(status="error", value=None, reason="rule_evaluation_timeout")
        except asyncio.CancelledError:
            await _kill_and_reap(process)
            raise
        if process.returncode != 0 or len(output) > MAX_RESULT_BYTES:
            return ScoreValue(status="error", value=None, reason="rule_worker_failed")
        try:
            return ScoreValue.model_validate_json(output)
        except ValueError:
            return ScoreValue(status="error", value=None, reason="rule_worker_protocol_error")


def _worker():
    from app.domain.evaluation.rule_engine import evaluate_rule

    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("rule_payload_limit")
    value = json.loads(raw)
    evidence = value["evidence"]
    evidence["citations"] = tuple(ResourceIdentity.model_validate(r) for r in evidence["citations"])
    evidence["available_sources"] = tuple(
        ResourceIdentity.model_validate(r) for r in evidence["available_sources"]
    )
    evidence["artifacts"] = tuple(
        ArtifactEvidence(ResourceIdentity.model_validate(a["resource"]), a["kind"], a["structure"])
        for a in evidence["artifacts"]
    )
    if evidence["recording"] is not None:
        evidence["recording"] = RecordingEvidence.model_validate(evidence["recording"])
    score = evaluate_rule(
        value["rule"],
        value["subject"] if value["subject_available"] else MISSING,
        value["reference"],
        RuleEvidence(**evidence),
    )
    sys.stdout.buffer.write(_encode(score.model_dump(mode="json"), MAX_RESULT_BYTES))


if __name__ == "__main__":
    _worker()
