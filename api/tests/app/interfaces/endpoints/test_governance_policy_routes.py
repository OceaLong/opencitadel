"""Governance policy writes append revisions using head CAS."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.scope import Principal
from app.interfaces.auth_dependencies import require_admin
from app.interfaces.endpoints.governance_policy_routes import router
from app.interfaces.service_dependencies import get_identity_runtime


class Governance:
    def __init__(self) -> None:
        self.updated = None

    async def get_active(self):
        return {"generation": 3, "digest": "a" * 64, "policy": {}}

    async def update(self, policy, *, expected_generation, actor_user_id, note):
        self.updated = (policy, expected_generation, actor_user_id, note)
        return {"generation": 4, "digest": "b" * 64, "policy": policy}


def test_policy_read_and_cas_update_are_admin_governance_actions() -> None:
    governance = Governance()
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_identity_runtime] = lambda: SimpleNamespace(
        governance=governance
    )
    application.dependency_overrides[require_admin] = lambda: Principal(
        user_id="admin-1", global_role="admin"
    )
    client = TestClient(application)
    policy = {
        "effectTimeoutSeconds": 300,
        "effectMaxAttempts": 3,
        "approvalTtlSeconds": 86400,
        "workerConcurrency": 8,
        "retentionDays": 30,
        "snapshotInterval": 50,
        "safetyOverrides": {"tool.call": "non_idempotent_write"},
        "userQuotaDefaults": {},
        "teamQuotaDefaults": {},
    }

    assert client.get("/api/governance-policy").json()["data"]["generation"] == 3
    response = client.put(
        "/api/governance-policy",
        json={"expectedGeneration": 3, "note": "tighten", "policy": policy},
    )

    assert response.status_code == 200
    assert governance.updated[1:] == (3, "admin-1", "tighten")
