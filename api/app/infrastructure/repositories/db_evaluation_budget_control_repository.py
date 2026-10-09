"""Kernel-login-only immutable preadmission records in the caller transaction."""

from sqlalchemy import text

from app.domain.evaluation.budget_binding import BudgetNamespace, BudgetRunBinding
from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


class DBEvaluationBudgetControlRepository:
    def __init__(self, session):
        self.db = session

    async def namespace(self, scope, identity, *, lock=False):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT body,revision,state FROM evaluation_budget_namespaces WHERE scope_key=:scope AND id=:id"
                        + (" FOR UPDATE" if lock else "")
                    ),
                    params(scope, id=identity),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise DatasetNotFound("budget_namespace_unavailable")
        return BudgetNamespace.model_validate(
            {**row["body"], "revision": row["revision"], "state": row["state"]}
        )

    async def create(self, scope, value):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_budget_namespaces(id,scope_key,body) VALUES(:id,:scope,CAST(:body AS jsonb)) ON CONFLICT DO NOTHING"
            ),
            params(scope, id=value.id, body=value.model_dump_json(exclude={"state", "revision"})),
        )
        saved = await self.namespace(scope, value.id, lock=True)
        if saved.model_dump(exclude={"state", "revision"}) != value.model_dump(
            exclude={"state", "revision"}
        ):
            raise DatasetConflict("budget_namespace_changed")
        return saved

    async def binding(self, scope, run_id):
        body = await self.db.scalar(
            text(
                "SELECT body FROM evaluation_budget_bindings WHERE scope_key=:scope AND run_id=:run"
            ),
            params(scope, run=run_id),
        )
        return BudgetRunBinding.model_validate(body) if body else None

    async def bind(self, scope, value):
        await self.db.execute(
            text(
                "INSERT INTO evaluation_budget_bindings(run_id,namespace_id,scope_key,source_entity_id,purpose,body) "
                "VALUES(:run,:namespace,:scope,:source,:purpose,CAST(:body AS jsonb)) ON CONFLICT DO NOTHING"
            ),
            params(
                scope,
                run=value.run_id,
                namespace=value.namespace_id,
                source=value.source_entity_id,
                purpose=value.purpose,
                body=value.model_dump_json(),
            ),
        )
        previous = await self.binding(scope, value.run_id)
        if previous != value:
            raise DatasetConflict("budget_binding_changed")
        return previous

    async def close(self, scope, identity, *, expected_revision):
        previous = await self.namespace(scope, identity, lock=True)
        if previous.state == "closed" and previous.revision == expected_revision + 1:
            return previous
        if previous.state != "open" or previous.revision != expected_revision:
            raise DatasetConflict("budget_namespace_revision_changed")
        active = await self.db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM evaluation_judge_intents i JOIN evaluation_judge_work w ON w.scope_key=i.scope_key AND w.intent_id=i.id WHERE i.scope_key=:scope AND i.namespace_id=:id AND w.status IN ('pending','submitted'))"
            ),
            params(scope, id=identity),
        )
        if active:
            raise ValueError("judge_namespace_active")
        await self.db.execute(
            text(
                "UPDATE evaluation_budget_namespaces SET state='closed',revision=revision+1 WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=identity),
        )
        return await self.namespace(scope, identity)
