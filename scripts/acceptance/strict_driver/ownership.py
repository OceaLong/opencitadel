"""Conservative read-only exclusion before any global claim or scheduler tick."""

from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_evaluation_batch_repository import effect_query

KERNEL = AuthorizationContext.system("execution-kernel")


def reject_foreign(rows, allowed):
    foreign = {(str(kind), str(identity)) for kind, identity in rows} - allowed
    if foreign:
        # IDs only. Never emit payloads or source SQL errors to browser.
        raise RuntimeError("foreign eligible work: " + repr(sorted(foreign)))


async def assert_exclusive(factory, allowed):
    async with factory(KERNEL) as work:
        # Deliberately include unexpired claims: they may become eligible while
        # this driver runs. Terminal batches with late effects are also work.
        batches = await work.db_session.execute(
            text(f"""
            SELECT 'batch', id::text FROM evaluation_batches source
            WHERE status NOT IN ('rejected','completed','completed_with_errors','failed','cancelled')
               OR cleanup_status!='clean' OR EXISTS (
                 SELECT 1 FROM evaluation_batch_results result
                 JOIN evaluation_batch_attempts attempt ON attempt.scope_key=result.scope_key
                   AND attempt.result_id=result.id AND attempt.attempt=result.attempt
                 WHERE result.scope_key=source.scope_key AND result.batch_id=source.id
                   AND NOT result.unknown_effect AND ({effect_query("source.scope_key", "attempt.run_id")}))
        """)
        )
        activities = await work.db_session.execute(
            text("""
            SELECT 'run', run_id::text FROM execution_activity_tasks
            WHERE status IN ('pending','claimed','call_started')
        """)
        )
        environments = await work.db_session.execute(
            text("""
            SELECT 'lease', id::text FROM evaluation_environment_leases
            WHERE state!='verified_clean'
        """)
        )
        reject_foreign([*batches.all(), *activities.all(), *environments.all()], allowed)
