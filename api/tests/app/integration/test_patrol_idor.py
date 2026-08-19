from types import SimpleNamespace

import pytest

from app.domain.errors import NotFoundError
from app.application.patrol_templates import load_patrol_template
from app.application.services.patrol_pack_service import PatrolPackService
from app.application.services.patrol_run_service import PatrolRunService
from app.domain.models.patrol import PatrolPack, PatrolRun, PatrolTriggerType
from app.domain.models.scope import OwnerScope


class Repo:
    def __init__(self):
        config = load_patrol_template("kubernetes-baseline-v1")
        self.pack = PatrolPack(owner_user_id="owner-a", name="Daily", slug="daily", config=config, mcp_server_id="server-1", skill_id="skill-1")
        self.run = PatrolRun(pack_id=self.pack.id, pack_version=1, pack_snapshot={"config": config.model_dump(mode="json")}, trigger_type=PatrolTriggerType.MANUAL, idempotency_key="key-1")

    async def get_pack(self, pack_id, scope=None, for_update=False):
        return self.pack if pack_id == self.pack.id and scope and scope.user_id == self.pack.owner_user_id else None

    async def get_run(self, run_id, scope=None, for_update=False):
        return self.run if run_id == self.run.id and scope and scope.user_id == self.pack.owner_user_id else None


class Uow:
    def __init__(self, repo):
        self.patrol = repo
        self.session = SimpleNamespace()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None


@pytest.mark.asyncio
async def test_cross_owner_pack_and_run_ids_are_indistinguishable_from_missing():
    repo = Repo()
    pack_service = PatrolPackService(lambda: Uow(repo))
    run_service = PatrolRunService(lambda: Uow(repo))
    attacker = OwnerScope.personal("owner-b")
    with pytest.raises(NotFoundError):
        await pack_service.get_pack(repo.pack.id, attacker)
    with pytest.raises(NotFoundError):
        await run_service.get_run(repo.run.id, attacker)
