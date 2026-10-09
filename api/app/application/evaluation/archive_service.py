"""Archive discovery resources while retaining immutable evidence and pins."""

from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext

KINDS = frozenset({"dataset", "config", "rubric", "suite", "recording", "environment", "batch"})


class ArchiveService:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def archive(self, scope, principal, *, kind, identity, expected_revision, request_id):
        if kind not in KINDS:
            raise ValueError("archive_kind_invalid")
        authorization = AuthorizationContext.for_principal(
            principal, scope=scope, request_id=request_id
        )
        async with self.uow_factory(authorization) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=True)
            receipt = await work.evaluation_archive.archive(
                scope,
                principal,
                kind=kind,
                identity=identity,
                expected_revision=expected_revision,
                request_id=request_id,
            )
            if receipt.pop("new", False):
                await work.audit.add_archive(
                    AuditLog(
                        actor_user_id=principal.user_id,
                        team_id=scope.team_id,
                        action="evaluation.archive",
                        resource_type="evaluation_" + kind,
                        resource_id=str(identity),
                        request_id=request_id,
                        metadata={"revision": expected_revision},
                    ),
                    authorization=authorization,
                )
            await work.commit()
            return receipt
