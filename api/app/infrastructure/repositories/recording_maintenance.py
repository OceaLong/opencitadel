"""Kernel inventory only; generation re-enters the original current principal scope."""

from sqlalchemy import text

from app.application.evaluation.recording_worker import RecordingWorker
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal


class RecordingMaintenance:
    def __init__(self, uow_factory, service_factory, lifecycle):
        self.uow_factory, self.service_factory, self.lifecycle = (
            uow_factory,
            service_factory,
            lifecycle,
        )

    async def process_pending(self):
        async with self.uow_factory() as uow:
            jobs = (
                (
                    await uow.db_session.execute(
                        text(
                            "SELECT id,owner_user_id,team_id,principal,status,revision,claim_token,lease_until FROM evaluation_recording_jobs WHERE status IN ('queued','running') AND (lease_until IS NULL OR lease_until<CURRENT_TIMESTAMP) ORDER BY created_at LIMIT 5"
                        )
                    )
                )
                .mappings()
                .all()
            )
        for job in jobs:
            principal = Principal.model_validate(job["principal"])
            scope = (
                OwnerScope.team(principal.user_id, job["team_id"])
                if job["team_id"]
                else OwnerScope.personal(job["owner_user_id"])
            )
            auth = AuthorizationContext.for_principal(principal, scope=scope)
            service = self.service_factory(auth)
            try:
                async with service.uow_factory(auth) as current:
                    await current.evaluation_dataset.authorize(scope, principal, write=True)
            except PermissionError:
                # Only this actual current-auth check can authorize lifecycle termination.
                async with self.uow_factory() as terminal:
                    if await terminal.evaluation_recording.fail_revoked_inventory(scope, job):
                        await terminal.audit.add(
                            AuditLog(
                                action="evaluation.recording.revoked",
                                resource_type="evaluation_recording",
                                resource_id=str(job["id"]),
                                team_id=scope.team_id,
                                metadata={
                                    "lifecycle_actor": "recording-maintenance",
                                    "requester_user_id": principal.user_id,
                                    "reason": "recording_authority_revoked",
                                },
                            )
                        )
                        await terminal.commit()
                continue
            worker = RecordingWorker(service, self.lifecycle)
            await worker.generate(scope, principal, job["id"])
        return len(jobs)
