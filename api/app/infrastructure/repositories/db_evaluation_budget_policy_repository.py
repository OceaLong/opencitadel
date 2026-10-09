"""Versioned physical limits; activation never resets counters or unknown holds."""

from sqlalchemy import text

from app.domain.evaluation.budget import BudgetPolicy


class DBEvaluationBudgetPolicyRepository:
    def __init__(self, session):
        self.db = session

    async def active(self, *, lock=False):
        body = await self.db.scalar(
            text(
                "SELECT v.body FROM evaluation_physical_policy_head h JOIN evaluation_physical_policy_versions v ON v.revision=h.revision WHERE h.singleton"
                + (" FOR UPDATE OF h" if lock else "")
            )
        )
        return BudgetPolicy.model_validate(body) if body is not None else None

    async def bootstrap(self, policy):
        current = await self.active(lock=True)
        if current is None:
            if policy.revision != 1:
                raise ValueError("budget_policy_changed")
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_physical_policy_versions(revision,body) VALUES(1,CAST(:body AS jsonb)) ON CONFLICT DO NOTHING"
                ),
                {"body": policy.model_dump_json()},
            )
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_physical_policy_head(singleton,revision) VALUES(true,1) ON CONFLICT DO NOTHING"
                )
            )
            current = await self.active(lock=True)
        if current != policy:
            raise ValueError("budget_policy_changed")
        return current

    async def activate(self, policy, *, expected_revision):
        current = await self.active(lock=True)
        if current == policy and expected_revision == policy.revision - 1:
            return current
        if (
            current is None
            or current.revision != expected_revision
            or policy.revision != expected_revision + 1
        ):
            raise ValueError("budget_policy_changed")
        await self.db.execute(
            text(
                "INSERT INTO evaluation_physical_policy_versions(revision,body) VALUES(:revision,CAST(:body AS jsonb))"
            ),
            {"revision": policy.revision, "body": policy.model_dump_json()},
        )
        await self.db.execute(
            text("UPDATE evaluation_physical_policy_head SET revision=:revision WHERE singleton"),
            {"revision": policy.revision},
        )
        # The head lock precedes every reserve/settle/unknown counter lock. Only
        # mutable physical slots change; batch budgets and accounting stay fixed.
        for pattern, value in (
            ("0:global", policy.global_concurrency),
            ("1:provider:%", policy.provider_concurrency),
            ("2:user:%", policy.user_concurrency),
        ):
            await self.db.execute(
                text(
                    "UPDATE evaluation_budget_buckets SET limits=(limits-'slots') || CASE WHEN CAST(:value AS integer) IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('slots',CAST(:value AS integer)) END WHERE key LIKE :pattern"
                ),
                {"value": value, "pattern": pattern},
            )
        return policy
