from alembic.config import Config
from alembic.script import ScriptDirectory

from app.domain.models.patrol import (
    PatrolRemediation,
    PatrolRemediationAction,
    PatrolRemediationStatus,
)
from app.infrastructure.models.patrol import PatrolRemediationModel


def test_patrol_identity_migration_extends_lease_proof():
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "0033patrol_effect_identity"
    )
    assert revision.down_revision == "0032activity_lease_proof"


def test_patrol_provider_identity_and_receipt_survive_orm_round_trip():
    item = PatrolRemediation(
        pack_id="pack",
        run_id="run",
        finding_id="finding",
        check_result_id="check",
        fingerprint="f" * 64,
        action=PatrolRemediationAction.RESTART_WORKLOAD,
        target_namespace="demo",
        target_workload="api",
        params_hash="a" * 64,
        idempotency_key="rem:1",
        created_by="owner",
        status=PatrolRemediationStatus.UNKNOWN,
        actuator_operation_id="operation",
        actuator_payload_digest="b" * 64,
        actuator_target_uid="uid",
        actuator_target_ref="cluster",
        actuator_receipt={"state": "applied", "revision": 1},
    )
    assert PatrolRemediationModel.from_domain(item).to_domain() == item
    clause = next(
        index
        for index in PatrolRemediationModel.__table__.indexes
        if index.name == "uq_patrol_remediations_active_finding"
    ).dialect_options["postgresql"]["where"]
    assert str(clause) == "status NOT IN ('verified', 'failed', 'cancelled')"
