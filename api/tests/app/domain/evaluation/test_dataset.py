import pytest


def test_duplicate_case_keys_reject_whole_import():
    from app.domain.evaluation.dataset import validate_case_keys

    with pytest.raises(ValueError, match="duplicate case_key"):
        validate_case_keys([{"case_key": "a"}, {"case_key": "a"}])


def test_case_is_deeply_immutable_and_preserves_scoring_metadata():
    from app.domain.evaluation.dataset import CaseRevision

    case = CaseRevision(
        case_key="a",
        input="question",
        tags=["t"],
        applicable_dimensions=["correctness"],
        rules=[{"kind": "text_exact", "expected": "x"}],
    )
    assert case.tags == ("t",)
    assert case.applicable_dimensions == ("correctness",)
    with pytest.raises(TypeError):
        case.rules[0]["expected"] = "changed"


def test_role_injection_and_unconfirmed_reference_are_rejected():
    from app.domain.evaluation.dataset import CaseRevision, validate_publication

    with pytest.raises(ValueError, match="role"):
        CaseRevision(case_key="a", input=[{"role": "system", "content": "elevate"}])
    candidate = CaseRevision(
        case_key="a", input="x", reference_answer="output", reference_confirmed=False
    )
    with pytest.raises(ValueError, match="reference_unconfirmed"):
        validate_publication([candidate])
    validate_publication([CaseRevision(case_key="a", input="x")])


def test_redacted_from_run_input_requires_explicit_edit_confirmation():
    from app.domain.evaluation.dataset import CaseRevision, validate_publication

    case = CaseRevision(
        case_key="a",
        input="[redacted]",
        source_run_id="00000000-0000-0000-0000-000000000001",
        input_status="sanitized",
    )
    with pytest.raises(ValueError, match="input_confirmation_required"):
        validate_publication([case])


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "python", "script": "danger"},
        {"kind": "text_exact", "expected": "x", "script": "danger"},
        {"kind": "jsonpath", "path": "$..secret", "op": "eq", "expected": 1},
        {"kind": "json_schema", "schema": {"$ref": "https://outside.invalid/schema"}},
        {"kind": "json_schema", "schema": {"$ref": "#/$defs/missing"}},
        {"kind": "json_schema", "schema": {"$ref": "#"}},
    ],
)
def test_publication_rejects_unvalidated_or_network_rules(rule):
    from app.domain.evaluation.dataset import CaseRevision, validate_publication

    with pytest.raises(ValueError, match=r"rule|schema|reference"):
        validate_publication([CaseRevision(case_key="a", input="x", rules=[rule])])


def test_reference_rule_requires_confirmed_reference_and_local_schema_is_valid():
    from app.domain.evaluation.dataset import CaseRevision, validate_publication

    with pytest.raises(ValueError, match="reference_required"):
        validate_publication(
            [
                CaseRevision(
                    case_key="a",
                    input="x",
                    rules=[{"kind": "text_exact", "reference_required": True}],
                )
            ]
        )
    validate_publication(
        [
            CaseRevision(
                case_key="a",
                input="x",
                rules=[
                    {
                        "kind": "json_schema",
                        "schema": {"$defs": {"text": {"type": "string"}}, "$ref": "#/$defs/text"},
                    }
                ],
            )
        ]
    )


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": {}},
        {"kind": "jsonpath", "path": "$.answer", "op": {}},
        {"kind": "artifact", "artifact_kind": {}},
    ],
)
def test_malformed_rule_discriminators_are_controlled(rule):
    from app.domain.evaluation.dataset import CaseRevision, validate_publication

    with pytest.raises(ValueError, match=r"rule|artifact"):
        validate_publication([CaseRevision(case_key="a", input="x", rules=[rule])])


def test_schema_preflight_precedes_recursive_validator(monkeypatch):
    from jsonschema import Draft202012Validator

    from app.domain.evaluation.rule_validation import validate_rule_definition

    schema = {"type": "string"}
    for _ in range(100):
        schema = {"properties": {"x": schema}}

    def must_not_run(*args, **kwargs):
        raise AssertionError("recursive validator ran before complexity guard")

    monkeypatch.setattr(Draft202012Validator, "check_schema", must_not_run)
    with pytest.raises(ValueError, match="schema_complexity_exceeded"):
        validate_rule_definition({"kind": "json_schema", "schema": schema})
