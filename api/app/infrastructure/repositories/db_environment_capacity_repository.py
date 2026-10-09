"""Serialize new environment occupancy; derive holds from E04 non-clean leases."""

from sqlalchemy import text

from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy


class DBEnvironmentCapacityRepository:
    def __init__(self, session):
        self.db = session

    async def active(self, *, lock=False):
        body = await self.db.scalar(
            text(
                "SELECT v.body FROM evaluation_environment_capacity_head h JOIN evaluation_environment_capacity_versions v ON v.revision=h.revision WHERE h.singleton"
                + (" FOR UPDATE OF h" if lock else "")
            )
        )
        return EnvironmentCapacityPolicy.model_validate(body) if body is not None else None

    async def bootstrap(self, policy):
        current = await self.active(lock=True)
        if current is None:
            if policy.revision != 1:
                raise ValueError("environment_capacity_policy_changed")
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_environment_capacity_versions(revision,body) VALUES(1,CAST(:body AS jsonb)) ON CONFLICT DO NOTHING"
                ),
                {"body": policy.model_dump_json()},
            )
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_environment_capacity_head(singleton,revision) VALUES(true,1) ON CONFLICT DO NOTHING"
                )
            )
            current = await self.active(lock=True)
        if current != policy:
            raise ValueError("environment_capacity_policy_changed")
        return current

    async def activate(self, policy, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("environment_capacity_policy_changed")
        current = await self.active(lock=True)
        if current == policy and expected_revision == policy.revision - 1:
            return current
        if (
            type(expected_revision) is not int
            or current is None
            or current.revision != expected_revision
            or policy.revision != expected_revision + 1
        ):
            raise ValueError("environment_capacity_policy_changed")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_environment_capacity_versions(revision,body) VALUES(:revision,CAST(:body AS jsonb))"
            ),
            {"revision": policy.revision, "body": policy.model_dump_json()},
        )
        await self.db.execute(
            text(
                "UPDATE evaluation_environment_capacity_head SET revision=:revision WHERE singleton"
            ),
            {"revision": policy.revision},
        )
        return policy

    async def _require_kernel(self):
        # The public API can register/repair but cannot allocate. Check this
        # before reading any cross-workspace occupancy, without broader grants.
        allowed = await self.db.scalar(
            text(
                "SELECT public.opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND has_table_privilege(current_user,'evaluation_environment_leases','INSERT')"
            )
        )
        if not allowed:
            raise PermissionError("environment_allocation_kernel_required")

    async def lock_allocation(self, policy):
        await self._require_kernel()
        await self.bootstrap(policy)
        await self._require_kernel()

    async def check(self, scope, lease, policy, *, requested_limit):
        if lease.requester:
            from app.domain.models.scope import Principal
            from app.infrastructure.repositories.db_evaluation_dataset_repository import (
                DBEvaluationDatasetRepository,
            )

            await DBEvaluationDatasetRepository(self.db).authorize(
                scope, Principal.model_validate(lease.requester), write=True
            )
        workspace = "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id
        requester = lease.requester.get("user_id") or (
            scope.user_id if not scope.team_id else "legacy_unknown_requester"
        )
        counts = (
            (
                await self.db.execute(
                    text("""SELECT count(*) AS total,
            count(*) FILTER(WHERE scope_key=:scope) AS workspace,
            count(*) FILTER(WHERE coalesce(nullif(requester->>'user_id',''),owner_user_id,'legacy_unknown_requester')=:requester) AS user_count
            FROM evaluation_environment_leases WHERE state <> 'verified_clean'"""),
                    {"scope": workspace, "requester": requester},
                )
            )
            .mappings()
            .one()
        )
        for count, limit, kind in (
            (counts["total"], policy.global_limit, "global"),
            (counts["user_count"], policy.user_limit, "user"),
            (counts["workspace"], min(policy.workspace_limit, requested_limit), "workspace"),
        ):
            if limit is not None and count >= limit:
                raise ValueError("environment_" + kind + "_capacity_exhausted")
