import pytest

from app.domain.evaluation.rule_engine import RuleEvidence, evaluate_rule, exact_match


def test_normalization_is_explicit():
    assert exact_match(" A  B ", "A B", normalize=True)
    assert not exact_match(" A  B ", "A B", normalize=False)
    assert not exact_match("a", "A", normalize=True)


@pytest.mark.parametrize(
    ("rule", "subject", "passed"),
    [
        ({"kind": "text_exact", "expected": "ok"}, "no", False),
        ({"kind": "text_normalized", "expected": "A B"}, " A  B ", True),
        (
            {"kind": "jsonpath", "path": "$.items[0].v", "op": "eq", "expected": None},
            {"items": [{"v": None}]},
            True,
        ),
        ({"kind": "jsonpath", "path": "$.items[0]", "op": "exists"}, {"items": []}, False),
        ({"kind": "jsonpath", "path": "$.v", "op": "eq", "expected": 1}, {"v": True}, False),
        ({"kind": "jsonpath", "path": "$.v", "op": "ne", "expected": 1}, {}, False),
        ({"kind": "required_fields", "fields": ["$.v"]}, {"v": None}, True),
        ({"kind": "required_fields", "fields": ["$.v"]}, {}, False),
        ({"kind": "json_schema", "schema": {"type": "array", "minItems": 1}}, [], False),
        (
            {
                "kind": "json_schema",
                "schema": {"$defs": {"v": {"type": "integer"}}, "$ref": "#/$defs/v"},
            },
            2,
            True,
        ),
    ],
)
def test_business_false_is_valid(rule, subject, passed):
    score = evaluate_rule(rule, subject, None, RuleEvidence())
    assert score.status == "valid"
    assert score.value is passed


def test_missing_reference_and_evidence_are_not_zero():
    for rule in (
        {"kind": "text_exact", "reference_required": True},
        {"kind": "citations"},
        {"kind": "artifact", "artifact_kind": "doc"},
    ):
        score = evaluate_rule(rule, "answer", None, RuleEvidence(available=False))
        assert score.status == "not_evaluable"
        assert score.value is None


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "json_schema", "schema": {"$ref": "https://invalid.example/schema"}},
        {"kind": "jsonpath", "path": "$..v", "op": "exists"},
        {"kind": "python", "code": "1"},
    ],
)
def test_invalid_runtime_configuration_is_scoring_error(rule):
    score = evaluate_rule(rule, {}, None, RuleEvidence())
    assert score.status == "error"
    assert score.value is None


def test_fixed_citation_version_unavailable_differs_from_absent_citation():
    from app.domain.models.resource_pin import ResourceIdentity

    source = ResourceIdentity(
        resource_kind="knowledge_base", resource_id="kb", resource_version="v1"
    )
    rule = {"kind": "citations", "sources": [source.model_dump()]}
    assert evaluate_rule(rule, "answer", None, RuleEvidence()).status == "not_evaluable"
    score = evaluate_rule(rule, "answer", None, RuleEvidence(available_sources=(source,)))
    assert score.status == "valid"
    assert score.value is False
    assert (
        evaluate_rule(
            rule, "answer", None, RuleEvidence(available_sources=(source,), citations=(source,))
        ).value
        is True
    )


def test_artifact_kind_and_structure_use_fixed_evidence():
    from app.domain.evaluation.rule_engine import ArtifactEvidence
    from app.domain.models.resource_pin import ResourceIdentity

    rule = {
        "kind": "artifact",
        "artifact_kind": "doc",
        "schema": {"type": "object", "required": ["title"]},
    }
    source = ResourceIdentity(resource_kind="artifact", resource_id="a", resource_version="1")
    assert evaluate_rule(rule, "answer", None, RuleEvidence()).value is False
    assert (
        evaluate_rule(
            rule,
            "answer",
            None,
            RuleEvidence(artifacts=(ArtifactEvidence(source, "doc", {"title": "fixed"}),)),
        ).value
        is True
    )
    assert (
        evaluate_rule(
            rule,
            "answer",
            None,
            RuleEvidence(artifacts=(ArtifactEvidence(source, "web", {"title": "fixed"}),)),
        ).value
        is False
    )


def test_simulation_marker_requires_write_policy_and_canonical_full_tool_result():
    import json

    from app.domain.evaluation.rule_engine import corroborate_simulated_effect

    body = {
        "kind": "tool",
        "message": {
            "role": "tool",
            "content": json.dumps({"simulated_effect": True, "recording_revision": 2}),
        },
    }
    assert corroborate_simulated_effect(body, simulated_slot=True, write_effect=True, revision=2)
    assert not corroborate_simulated_effect(
        body, simulated_slot=False, write_effect=False, revision=2
    )
    for output, slot, write, revision in [
        (body, True, True, 1),
        (body, False, True, 2),
        ({"kind": "model", "message": body["message"]}, True, True, 2),
        ({"kind": "tool", "message": {"role": "tool", "content": "[]"}}, True, True, 2),
    ]:
        with pytest.raises(ValueError, match="recording_evidence_invalid"):
            corroborate_simulated_effect(
                output, simulated_slot=slot, write_effect=write, revision=revision
            )
