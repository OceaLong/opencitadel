from uuid import uuid4

import pytest


def test_matrix_limits_and_strict_integer_dimensions():
    from app.domain.evaluation.configuration import validate_matrix

    assert validate_matrix(1000, 5, 1) == 5000
    for args in [(1000, 5, 2), (1, 1, True), (1.0, 1, 1), (0, 1, 1), (1, 6, 1)]:
        with pytest.raises(ValueError, match=r"invalid matrix|matrix exceeds"):
            validate_matrix(*args)


def test_suite_requires_positive_budget_and_enforces_deployment_ceiling():
    from app.domain.evaluation.configuration import DeploymentLimits, SuiteSettings

    for value in [None, 0, -1, True, 1.5]:
        with pytest.raises(ValueError, match="token_budget"):
            SuiteSettings(token_budget=value)
    settings = SuiteSettings(token_budget=100)
    assert (settings.case_timeout_seconds, settings.batch_timeout_seconds) == (1800, 86400)
    assert (
        settings.subject_concurrency,
        settings.judge_concurrency,
        settings.environment_concurrency,
    ) == (5, 2, 2)
    with pytest.raises(ValueError, match="deployment_limit"):
        settings.validate_limits(DeploymentLimits(subject_concurrency=4))


def test_rubric_defaults_all_anchors_and_conditional_reference_validation():
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.rubric import RubricDefinition, validate_references

    rubric = RubricDefinition(judge_config_version=uuid4())
    assert len(rubric.dimensions) == 3
    assert all(len(d.anchors) == 5 and all(d.anchors) for d in rubric.dimensions)
    no_reference = CaseRevision(
        case_key="one", input="question", applicable_dimensions=("completeness",)
    )
    conditional = RubricDefinition(
        judge_config_version=uuid4(),
        reference_policy="required_when_applicable",
        reference_dimensions=("correctness",),
    )
    validate_references(conditional, (no_reference,))
    with pytest.raises(ValueError, match="reference_required"):
        validate_references(conditional, (CaseRevision(case_key="two", input="question"),))
    with pytest.raises(ValueError, match="anchors"):
        RubricDefinition(
            judge_config_version=uuid4(), dimensions=[{"id": "x", "name": "x", "anchors": ["bad"]}]
        )


def test_config_controls_reject_governance_override_and_judge_tools():
    from app.domain.evaluation.configuration import ConfigSelection

    with pytest.raises(ValueError, match="override_base_rules"):
        ConfigSelection(model_id="m", override_base_rules=True)
    with pytest.raises(ValueError, match="judge_tool_free"):
        ConfigSelection(model_id="m", purpose="evaluation_judge", tool_names=("shell_exec",))
    with pytest.raises(ValueError, match="temperature"):
        ConfigSelection(model_id="m", temperature=float("nan"))


def test_f07_bridge_uses_actual_policy_revision_and_never_changes_legacy_fingerprint():
    from datetime import UTC, datetime

    from app.application.evaluation.configuration_bridge import f07_configuration_evidence
    from app.domain.evaluation.configuration import ConfigSelection, ConfigVersion
    from app.domain.models.scope import OwnerScope
    from app.domain.runtime_policy import (
        ActiveExecutionPolicy,
        ExecutionPolicy,
        ExecutionPolicyRevision,
        RuntimePolicyHead,
        policy_digest,
    )

    revision = uuid4()
    policy = ExecutionPolicy()
    now = datetime.now(UTC)
    active = ActiveExecutionPolicy(
        head=RuntimePolicyHead(
            version=1,
            execution_revision_id=revision,
            operations_revision_id=uuid4(),
            updated_by="test",
            updated_at=now,
        ),
        revision=ExecutionPolicyRevision(
            id=revision,
            sequence=1,
            schema_version=1,
            policy=policy,
            digest=policy_digest(1, policy),
            created_by="test",
            note="test",
            created_at=now,
        ),
    )
    version = ConfigVersion(
        id=uuid4(),
        entity_id=uuid4(),
        revision=1,
        name="test",
        selection=ConfigSelection(model_id="m"),
        fingerprint="fixed",
        snapshot={
            "identity": {
                "model_id": "m",
                "configured_model": "alias",
                "provider": "openai",
                "endpoint_id": "endpoint",
            },
            "settings": {},
            "prompt": {},
            "contracts": [],
            "legacy_catalog_fingerprint": "legacy",
            "contract_digest": "full",
            "effective_policy": {"execution": policy.model_dump(mode="json")},
            "credential_ref": {"kind": "inference_endpoint", "id": "endpoint"},
            "price": {"input": None, "output": None},
        },
    )
    scope = OwnerScope.personal("user")
    body = f07_configuration_evidence(scope, version, active, purpose="evaluation_subject")
    assert body["policy_revision"] == str(revision)
    assert body["tools"]["fingerprint"] == "legacy"
    assert body["evaluation_configuration"]["contract_digest"] == "full"
    assert body["evaluation_configuration"]["id"] == str(version.id)
    with pytest.raises(ValueError, match="configuration_purpose_mismatch"):
        f07_configuration_evidence(scope, version, active, purpose="evaluation_judge")


def test_judge_configuration_cannot_select_agent_family():
    from app.domain.evaluation.configuration import ConfigSelection

    with pytest.raises(ValueError, match="judge_requires_ask"):
        ConfigSelection(model_id="m", purpose="evaluation_judge", mode="agent")
