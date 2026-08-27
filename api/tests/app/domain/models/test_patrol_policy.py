from app.domain.runtime_policy import (
    OperationsPolicy,
    PatrolAdmissionMode,
    PatrolRemediationMode,
)


def test_operations_policy_has_closed_patrol_policy() -> None:
    policy = OperationsPolicy()

    assert policy.patrol.admission is PatrolAdmissionMode.ACCEPTING
    assert policy.patrol.remediation is PatrolRemediationMode.DISABLED


def test_patrol_policy_rejects_unknown_modes() -> None:
    policy = OperationsPolicy.model_validate(
        {"patrol": {"admission": "paused", "remediation": "propose_only"}}
    )

    assert policy.patrol.admission is PatrolAdmissionMode.PAUSED
    assert policy.patrol.remediation is PatrolRemediationMode.PROPOSE_ONLY
