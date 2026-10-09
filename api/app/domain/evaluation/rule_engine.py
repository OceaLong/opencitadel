"""Bounded, offline evaluator registry for the E01 published wire grammar."""

import json
import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from referencing import Registry

from app.domain.evaluation.rule_validation import validate_rule_definition
from app.domain.evaluation.scoring import RecordingEvidence, ScoreValue
from app.domain.models.resource_pin import ResourceIdentity

MISSING = object()


@dataclass(frozen=True)
class ArtifactEvidence:
    resource: ResourceIdentity
    kind: str
    structure: object


@dataclass(frozen=True)
class RuleEvidence:
    """Only current-authorized, exact-version facts from the evidence reader belong here."""

    available: bool = True
    citations: tuple[ResourceIdentity, ...] = ()
    available_sources: tuple[ResourceIdentity, ...] = ()
    artifacts: tuple[ArtifactEvidence, ...] = ()
    simulated: bool = False
    recording_revision: str | None = None
    recording: RecordingEvidence | None = None


def exact_match(actual: str, expected: str, normalize: bool = False) -> bool:
    if normalize:
        return " ".join(actual.split()) == " ".join(expected.split())
    return actual == expected


def corroborate_simulated_effect(output, *, simulated_slot, write_effect, revision):
    """Inputs must already be tied to a durable consumed slot and accepted F06 output."""
    if simulated_slot != write_effect:
        raise ValueError("recording_evidence_invalid")
    if not simulated_slot:
        return False
    try:
        message = output["message"]
        result = json.loads(message["content"])
        if (
            output["kind"] != "tool"
            or message["role"] != "tool"
            or not isinstance(result, dict)
            or result.get("simulated_effect") is not True
            or type(result.get("recording_revision")) is not int
            or result["recording_revision"] != revision
        ):
            raise ValueError("recording_evidence_invalid")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("recording_evidence_invalid") from error
    return True


def _path(subject, path):
    for field, index in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]", path):
        if field:
            if not isinstance(subject, dict) or field not in subject:
                return MISSING
            subject = subject[field]
        else:
            if not isinstance(subject, (list, tuple)) or int(index) >= len(subject):
                return MISSING
            subject = subject[int(index)]
    return subject


def _equal(actual, expected):
    # JSON booleans are not numbers, including nested values.
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _equal(actual[k], expected[k]) for k in actual
        )
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _equal(a, b) for a, b in zip(actual, expected, strict=True)
        )
    return actual == expected


def _json(subject):
    if isinstance(subject, str):
        try:
            return json.loads(subject)
        except ValueError:
            return MISSING
    return subject


def _schema(subject, schema):
    # Publication disallows every external reference; registry also has no retrieval.
    return Draft202012Validator(schema, registry=Registry()).is_valid(subject)


def _text(rule, subject, reference, evidence):
    expected = rule.get("expected", reference)
    return (
        isinstance(subject, str)
        and isinstance(expected, str)
        and exact_match(subject, expected, rule["kind"] == "text_normalized")
    )


def _jsonpath(rule, subject, reference, evidence):
    actual = _path(_json(subject), rule["path"])
    if rule["op"] == "exists":
        return actual is not MISSING
    if actual is MISSING:
        return False
    expected = rule["expected"]
    if rule["op"] in {"eq", "ne"}:
        equal = _equal(actual, expected)
        return equal if rule["op"] == "eq" else not equal
    if type(actual) not in (int, float) or type(expected) not in (int, float):
        return False
    return {
        "gt": actual > expected,
        "gte": actual >= expected,
        "lt": actual < expected,
        "lte": actual <= expected,
    }[rule["op"]]


def _citations(rule, subject, reference, evidence):
    required = tuple(ResourceIdentity.model_validate(source) for source in rule.get("sources", ()))
    if any(source not in evidence.available_sources for source in required):
        return None
    return bool(evidence.citations) and all(source in evidence.citations for source in required)


def _artifact(rule, subject, reference, evidence):
    matching = [
        artifact for artifact in evidence.artifacts if artifact.kind == rule["artifact_kind"]
    ]
    return any(
        "schema" not in rule or _schema(artifact.structure, rule["schema"]) for artifact in matching
    )


_EVALUATORS = {
    "text_exact": _text,
    "text_normalized": _text,
    "jsonpath": _jsonpath,
    "json_schema": lambda r, s, ref, e: _json(s) is not MISSING and _schema(_json(s), r["schema"]),
    "required_fields": lambda r, s, ref, e: all(
        _path(_json(s), p) is not MISSING for p in r["fields"]
    ),
    "citations": _citations,
    "artifact": _artifact,
}


def evaluate_rule(rule, subject, reference, evidence: RuleEvidence) -> ScoreValue:
    try:
        validate_rule_definition(rule)
    except (ValueError, TypeError, RecursionError):
        return ScoreValue(status="error", value=None, reason="invalid_rule_configuration")
    if subject is MISSING or (rule.get("reference_required") and reference is None):
        return ScoreValue(
            status="not_evaluable", value=None, reason="subject_or_reference_unavailable"
        )
    if rule["kind"] in {"citations", "artifact"} and not evidence.available:
        return ScoreValue(status="not_evaluable", value=None, reason="evidence_unavailable")
    try:
        passed = _EVALUATORS[rule["kind"]](rule, subject, reference, evidence)
    except (ValueError, TypeError, RecursionError, KeyError):
        return ScoreValue(status="error", value=None, reason="rule_evaluation_error")
    if passed is None:
        return ScoreValue(status="not_evaluable", value=None, reason="fixed_source_unavailable")
    return ScoreValue(
        status="valid",
        value=passed,
        reason="rule_passed" if passed else "rule_failed",
        evidence=evidence.citations,
    )
