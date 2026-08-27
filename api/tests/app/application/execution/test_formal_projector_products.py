"""Product lifecycle reconciliation driven by the formal execution log."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.domain.execution.events import StoredEvent
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_formal_projector import (
    PostgresFormalProjector as FormalProjector,
)
from tests.app.execution_test_support import (
    execution_admin_session,
    run_policy_snapshot_json,
)


def _event(
    *,
    run_id: UUID,
    owner_user_id: str,
    version: int,
    event_type: str,
    occurred_at: datetime,
    public_payload: dict,
    semantic_payload: dict | None = None,
) -> StoredEvent:
    internal_payload = (
        {"semantic_payload": semantic_payload} if semantic_payload is not None else {}
    )
    if event_type == "RunCreated":
        internal_payload["policy_snapshot"] = run_policy_snapshot_json(public_payload["family"])
    return StoredEvent(
        event_id=uuid4(),
        stream_type="run",
        stream_id=str(run_id),
        stream_version=version,
        position=8_000_000_000 + version,
        event_type=event_type,
        event_schema_version=2 if event_type == "RunCreated" else 1,
        public_payload=public_payload,
        internal_payload=internal_payload,
        secret_ref=None,
        owner_user_id=owner_user_id,
        team_id=None,
        correlation_id=run_id,
        causation_id=None,
        occurred_at=occurred_at,
        prev_hash="0" * 64,
        event_hash=f"{version:x}" * 64,
    )


async def _insert_patrol_graph(
    session,
    *,
    owner_user_id: str,
    pack_id: str,
    patrol_run_id: str,
    execution_run_id: UUID,
    status: str = "running",
) -> None:
    server_id = f"server-{pack_id}"
    await session.execute(
        text("INSERT INTO users (id, email, username) VALUES (:id, :email, :username)"),
        {
            "id": owner_user_id,
            "email": f"{owner_user_id}@example.test",
            "username": owner_user_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO mcp_servers (id, name, owner_user_id) VALUES (:id, :name, :owner_user_id)"
        ),
        {
            "id": server_id,
            "name": server_id,
            "owner_user_id": owner_user_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO patrol_packs "
            "(id, owner_user_id, name, slug, status, config, mcp_server_id) "
            "VALUES "
            "(:id, :owner_user_id, :name, :slug, 'active', "
            "CAST(:config AS jsonb), :server_id)"
        ),
        {
            "id": pack_id,
            "owner_user_id": owner_user_id,
            "name": pack_id,
            "slug": pack_id,
            "config": json.dumps({"target_ref": "test", "checks": []}),
            "server_id": server_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO patrol_runs "
            "(id, pack_id, execution_run_id, pack_version, pack_snapshot, "
            "trigger_type, status, idempotency_key) VALUES "
            "(:id, :pack_id, :execution_run_id, 1, CAST(:snapshot AS jsonb), "
            "'manual', :status, :idempotency_key)"
        ),
        {
            "id": patrol_run_id,
            "pack_id": pack_id,
            "execution_run_id": execution_run_id,
            "snapshot": json.dumps({"config": {"target_ref": "test", "checks": []}}),
            "status": status,
            "idempotency_key": f"patrol:{patrol_run_id}",
        },
    )


async def _cleanup_patrol_graph(
    session,
    *,
    owner_user_id: str,
    pack_id: str,
    execution_run_id: UUID,
) -> None:
    await session.execute(
        text("DELETE FROM execution_run_projection WHERE run_id = :run_id"),
        {"run_id": execution_run_id},
    )
    await session.execute(
        text("DELETE FROM patrol_packs WHERE id = :pack_id"),
        {"pack_id": pack_id},
    )
    await session.execute(
        text("DELETE FROM mcp_servers WHERE id = :server_id"),
        {"server_id": f"server-{pack_id}"},
    )
    await session.execute(
        text("DELETE FROM users WHERE id = :owner_user_id"),
        {"owner_user_id": owner_user_id},
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_failed_formal_patrol_run_closes_running_product_run() -> None:
    owner_user_id = f"projector-user-{uuid4()}"
    pack_id = str(uuid4())
    patrol_run_id = str(uuid4())
    execution_run_id = uuid4()
    occurred_at = datetime(2026, 8, 24, 12, tzinfo=UTC)
    projector = FormalProjector(
        session_factory=None,  # type: ignore[arg-type]
        authorization=AuthorizationContext.system("product-test"),
    )

    async with execution_admin_session() as session:
        try:
            await _insert_patrol_graph(
                session,
                owner_user_id=owner_user_id,
                pack_id=pack_id,
                patrol_run_id=patrol_run_id,
                execution_run_id=execution_run_id,
            )
            await projector._project_run(
                session,
                _event(
                    run_id=execution_run_id,
                    owner_user_id=owner_user_id,
                    version=1,
                    event_type="RunCreated",
                    occurred_at=occurred_at,
                    public_payload={
                        "family": "patrol",
                        "source_entity_type": "patrol_run",
                        "source_entity_id": patrol_run_id,
                        "parent_run_id": None,
                        "input": {},
                    },
                    semantic_payload={"patrol_run_id": patrol_run_id},
                ),
            )
            await projector._project_run(
                session,
                _event(
                    run_id=execution_run_id,
                    owner_user_id=owner_user_id,
                    version=2,
                    event_type="RunFailed",
                    occurred_at=occurred_at + timedelta(seconds=5),
                    public_payload={"failure_code": "PATROL_COLLECTOR_MISSING"},
                ),
            )
            product = (
                await session.execute(
                    text("SELECT status, finished_at, summary FROM patrol_runs WHERE id = :id"),
                    {"id": patrol_run_id},
                )
            ).one()

            assert product.status == "failed"
            assert product.finished_at == occurred_at + timedelta(seconds=5)
            assert product.summary["error_code"] == "PATROL_COLLECTOR_MISSING"
            policy_metadata = (
                await session.execute(
                    text(
                        "SELECT execution_policy_revision_id, execution_policy_digest "
                        "FROM execution_run_projection WHERE run_id = :run_id"
                    ),
                    {"run_id": execution_run_id},
                )
            ).one()
            expected_snapshot = run_policy_snapshot_json("patrol")
            assert (
                str(policy_metadata.execution_policy_revision_id)
                == expected_snapshot["execution_revision_id"]
            )
            assert (
                policy_metadata.execution_policy_digest
                == expected_snapshot["execution_policy_digest"]
            )
        finally:
            await session.rollback()
            await _cleanup_patrol_graph(
                session,
                owner_user_id=owner_user_id,
                pack_id=pack_id,
                execution_run_id=execution_run_id,
            )
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "product_status",
        "terminal_event_type",
        "terminal_payload",
        "expected_status",
        "expected_error_code",
    ),
    [
        (
            "proposed",
            "RunCancelled",
            {"reason": "approval_rejected"},
            "cancelled",
            "approval_rejected",
        ),
        (
            "executing",
            "RunFailed",
            {"failure_code": "ACTIVITY_HANDLER_ERROR"},
            "failed",
            "ACTIVITY_HANDLER_ERROR",
        ),
    ],
)
@pytest.mark.usefixtures("_db_schema")
async def test_formal_remediation_terminal_event_closes_product_lifecycle(
    product_status: str,
    terminal_event_type: str,
    terminal_payload: dict,
    expected_status: str,
    expected_error_code: str,
) -> None:
    owner_user_id = f"remediation-user-{uuid4()}"
    pack_id = str(uuid4())
    patrol_run_id = str(uuid4())
    remediation_id = str(uuid4())
    session_id = f"session-{uuid4()}"
    check_result_id = str(uuid4())
    finding_id = str(uuid4())
    parent_execution_run_id = uuid4()
    execution_run_id = uuid4()
    occurred_at = datetime(2026, 8, 24, 13, tzinfo=UTC)
    projector = FormalProjector(
        session_factory=None,  # type: ignore[arg-type]
        authorization=AuthorizationContext.system("product-test"),
    )

    async with execution_admin_session() as session:
        try:
            await _insert_patrol_graph(
                session,
                owner_user_id=owner_user_id,
                pack_id=pack_id,
                patrol_run_id=patrol_run_id,
                execution_run_id=parent_execution_run_id,
                status="completed_with_findings",
            )
            await session.execute(
                text(
                    "INSERT INTO sessions (id, owner_user_id, status) "
                    "VALUES (:id, :owner_user_id, 'running')"
                ),
                {"id": session_id, "owner_user_id": owner_user_id},
            )
            await session.execute(
                text(
                    "INSERT INTO patrol_check_results "
                    "(id, run_id, check_id, status, severity, fingerprint, "
                    "started_at, finished_at) VALUES "
                    "(:id, :run_id, 'availability', 'fail', 'critical', "
                    ":fingerprint, :occurred_at, :occurred_at)"
                ),
                {
                    "id": check_result_id,
                    "run_id": patrol_run_id,
                    "fingerprint": "f" * 64,
                    "occurred_at": occurred_at,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO patrol_findings "
                    "(id, run_id, check_result_id, fingerprint, severity, "
                    "title, summary, first_seen_at, last_seen_at) VALUES "
                    "(:id, :run_id, :check_result_id, :fingerprint, "
                    "'critical', 'unavailable', 'unavailable', "
                    ":occurred_at, :occurred_at)"
                ),
                {
                    "id": finding_id,
                    "run_id": patrol_run_id,
                    "check_result_id": check_result_id,
                    "fingerprint": "f" * 64,
                    "occurred_at": occurred_at,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO patrol_remediations "
                    "(id, pack_id, run_id, finding_id, check_result_id, "
                    "fingerprint, session_id, action, target_namespace, "
                    "target_workload, params_hash, idempotency_key, status, "
                    "created_by) VALUES "
                    "(:id, :pack_id, :run_id, :finding_id, :check_result_id, "
                    ":fingerprint, :session_id, 'restart_workload', 'default', "
                    "'deployment/api', :params_hash, :idempotency_key, "
                    ":status, :created_by)"
                ),
                {
                    "id": remediation_id,
                    "pack_id": pack_id,
                    "run_id": patrol_run_id,
                    "finding_id": finding_id,
                    "check_result_id": check_result_id,
                    "fingerprint": "f" * 64,
                    "session_id": session_id,
                    "params_hash": "a" * 64,
                    "idempotency_key": f"rem:{remediation_id}",
                    "status": product_status,
                    "created_by": owner_user_id,
                },
            )
            await projector._project_run(
                session,
                _event(
                    run_id=execution_run_id,
                    owner_user_id=owner_user_id,
                    version=1,
                    event_type="RunCreated",
                    occurred_at=occurred_at,
                    public_payload={
                        "family": "remediation",
                        "source_entity_type": "session",
                        "source_entity_id": session_id,
                        "parent_run_id": str(parent_execution_run_id),
                        "input": {"remediation_id": remediation_id},
                    },
                    semantic_payload={
                        "session_id": session_id,
                        "remediation_id": remediation_id,
                    },
                ),
            )
            await projector._project_run(
                session,
                _event(
                    run_id=execution_run_id,
                    owner_user_id=owner_user_id,
                    version=2,
                    event_type=terminal_event_type,
                    occurred_at=occurred_at + timedelta(seconds=10),
                    public_payload=terminal_payload,
                ),
            )
            product = (
                await session.execute(
                    text("SELECT status, error_code FROM patrol_remediations WHERE id = :id"),
                    {"id": remediation_id},
                )
            ).one()

            assert product.status == expected_status
            assert product.error_code == expected_error_code
        finally:
            await session.rollback()
            await _cleanup_patrol_graph(
                session,
                owner_user_id=owner_user_id,
                pack_id=pack_id,
                execution_run_id=execution_run_id,
            )
            await session.execute(
                text("DELETE FROM execution_run_projection WHERE run_id = :run_id"),
                {"run_id": parent_execution_run_id},
            )
            await session.commit()
