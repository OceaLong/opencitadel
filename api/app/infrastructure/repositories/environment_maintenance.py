"""Bounded kernel maintenance for leases, cleanup and explicit admin repair intents."""

from app.application.evaluation.environment_service import EnvironmentService, EnvironmentWorker
from app.domain.models.scope import OwnerScope, Principal


def scope_for(row):
    return (
        OwnerScope.team(row["created_by"], row["team_id"])
        if row["team_id"]
        else OwnerScope.personal(row["owner_user_id"])
    )


class EnvironmentMaintenance:
    def __init__(self, uow_factory, registry):
        self.uow_factory = uow_factory
        self.service = EnvironmentService(uow_factory, registry)
        self.worker = EnvironmentWorker(uow_factory, registry)

    async def process_pending(self):
        async with self.uow_factory() as uow:
            repairs = await uow.evaluation_environment.repairs()
            expired = await uow.evaluation_environment.expired()
        for row in repairs:
            scope = scope_for(row)
            async with self.uow_factory() as uow:
                lease = await uow.evaluation_environment.lease(scope, row["lease_id"], lock=True)
                accepted = (
                    lease.state == "quarantine"
                    and lease.generation == row["generation"]
                    and lease.revision == row["lease_revision"]
                )
                if accepted:
                    try:
                        await self.service.cleanup_in_uow(
                            uow,
                            scope,
                            lease.id,
                            repair=True,
                            principal=Principal.model_validate(row["principal"]),
                        )
                    except (PermissionError, ValueError):
                        accepted = False
                await uow.evaluation_environment.settle_repair(scope, row["id"], accepted=accepted)
                await uow.commit()
        for row in expired:
            scope = scope_for(row)
            async with self.uow_factory() as uow:
                await self.service.cleanup_in_uow(uow, scope, row["id"])
                await uow.commit()
        async with self.uow_factory() as uow:
            pending = await uow.evaluation_environment.pending()
        for row in pending:
            await self.worker.process(scope_for(row), row["id"])
