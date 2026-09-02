"""Admin quota API exposes matching user/team dimensions."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models.scope import Principal
from app.interfaces.auth_dependencies import require_admin
from app.interfaces.endpoints.admin_routes import router
from app.interfaces.service_dependencies import get_identity_runtime


class Quotas:
    def __init__(self) -> None:
        self.updated = None

    async def get(self, kind, subject_id):
        return {
            "monthlyModelTokens": 1000,
            "dailyNewRuns": 5,
            "concurrentRuns": 2,
            "storageBytes": 4096,
        }

    async def set(self, kind, subject_id, limits, *, actor_user_id):
        self.updated = (kind, subject_id, limits, actor_user_id)
        return limits


def test_admin_can_read_and_update_the_same_team_quota_dimensions() -> None:
    quotas = Quotas()
    application = FastAPI()
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_identity_runtime] = lambda: SimpleNamespace(quotas=quotas)
    application.dependency_overrides[require_admin] = lambda: Principal(
        user_id="admin-1", global_role="admin"
    )
    client = TestClient(application)

    read = client.get("/api/admin/quotas/teams/team-1")
    updated = client.put(
        "/api/admin/quotas/teams/team-1",
        json={
            "monthlyModelTokens": 2000,
            "dailyNewRuns": 10,
            "concurrentRuns": 4,
            "storageBytes": 8192,
        },
    )

    assert read.status_code == 200
    assert set(read.json()["data"]) == {
        "monthlyModelTokens",
        "dailyNewRuns",
        "concurrentRuns",
        "storageBytes",
    }
    assert updated.status_code == 200
    assert quotas.updated[0:2] == ("team", "team-1")
    assert quotas.updated[3] == "admin-1"
