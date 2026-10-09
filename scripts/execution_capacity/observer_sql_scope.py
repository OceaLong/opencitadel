"""Finite, code-owned SELECT scope for the independent observer composition.

Literal operands come from the named existing owners; dynamic forms below have
closed substitutions. This never learns from an incoming query or report.
"""

from functools import lru_cache
from types import CodeType


def _literals(function):
    def walk(code):
        for item in code.co_consts:
            if isinstance(item, CodeType):
                yield from walk(item)
            elif isinstance(item, str) and item.lstrip().upper().startswith("SELECT "):
                yield item

    return set(walk(function.__code__))


@lru_cache(maxsize=1)
def fixed_texts():
    from scripts.execution_capacity import inventory_sql as sql
    from scripts.execution_capacity.batch_facts import (
        ATTEMPT_SQL,
        LEASE_SQL,
        OPERATION_SQL,
        BatchFacts,
    )
    from scripts.execution_capacity.cumulative_cleanup import TABLES, collect_settlement
    from scripts.execution_capacity.inventory_readback import persisted_steps, replay_run
    from scripts.execution_capacity.inventory_reader import (
        collect_source,
        read_dataset_objects,
    )
    from scripts.execution_capacity.persistence import PersistedFacts

    from app.infrastructure.execution import postgres_execution_view as view
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository as Batch,
    )
    from app.infrastructure.repositories.db_evaluation_dataset_repository import (
        DBEvaluationDatasetRepository as Dataset,
    )
    from app.infrastructure.repositories.db_resource_pin_repository import _SCOPE, _SESSION_SCOPE
    from app.infrastructure.repositories.db_resource_pin_repository import (
        DBResourcePinRepository as Pins,
    )

    result = {
        sql.IDENTITY,
        sql.OWNERS,
        sql.OWNERS_PAGE_FIRST,
        sql.OWNERS_PAGE_AFTER,
        sql.ATTEMPTS,
        sql.JUDGES,
        sql.PROJECTORS,
        LEASE_SQL,
        ATTEMPT_SQL,
        OPERATION_SQL,
    }
    for function in (
        view._coverage,
        view._step_orders,
        view.PostgresExecutionView.capture_run,
        view.PostgresExecutionView.active_generation,
        view.PostgresExecutionView.restore,
        Dataset.authorize,
        Dataset.get_version,
        Batch.get,
        Batch.counts,
        Batch.results,
        Pins.validate,
        Pins.resolve,
        BatchFacts.own_run,
        BatchFacts.own_object_parent,
        PersistedFacts.configuration,
        collect_source,
        read_dataset_objects,
        persisted_steps,
        replay_run,
        collect_settlement,
    ):
        result.update(_literals(function))
    result.update("SELECT * FROM " + table for table in TABLES)
    for table, predicate in (
        ("execution_poisoned_runs", "TRUE"),
        ("execution_poisoned_scopes", "TRUE"),
        ("execution_recovery_requests", "status NOT IN ('completed')"),
        ("execution_view_generations", "status IN ('building','failed')"),
    ):
        result.add(f"SELECT * FROM {table} WHERE {predicate}")
    result.update(
        f"SELECT body FROM evaluation_{kind}_versions WHERE scope_key=:scope AND id=:id"
        for kind in ("config", "rubric", "suite")
    )
    result.add(
        "SELECT c.*,o.storage_key,o.digest,o.cleaned_at FROM evaluation_version_cases m JOIN evaluation_case_revisions c ON c.scope_key=m.scope_key AND c.id=m.case_revision_id JOIN evaluation_object_intents o ON o.scope_key=c.scope_key AND o.id=c.object_id WHERE m.scope_key=:scope AND m.version_id=:version ORDER BY m.case_key"
    )
    result.add(
        "SELECT revision,body,digest FROM evaluation_environment_registry WHERE scope_key=:scope AND kind=:kind AND id=:id ORDER BY revision DESC LIMIT 1"
    )
    result.update(
        (
            f"SELECT id FROM knowledge_bases WHERE id=:id AND {_SCOPE} AND deleted_at IS NULL",
            f"SELECT id FROM sessions WHERE id=:session AND {_SESSION_SCOPE} AND deleted_at IS NULL",
            f"SELECT key,content_digest FROM files WHERE id=:id AND {_SCOPE} AND content_digest=:version AND content_available",
            f"SELECT content_id,citation_refs FROM execution_public_content WHERE content_id::text=:id AND {_SCOPE} AND content_digest=:version AND EXISTS (SELECT 1 FROM execution_content_bindings b WHERE b.content_id=execution_public_content.content_id AND b.scope_key=execution_public_content.scope_key)",
            "SELECT * FROM execution_view_shadow_steps WHERE scope_key=:scope AND run_id=:run AND generation=CAST(:identity AS uuid) ORDER BY step_id",
        )
    )
    # Literal fragments in controlled concatenations are not complete queries.
    return frozenset(
        value
        for value in result
        if " FROM " in value.upper()
        and not any(
            word in value.lower()
            for word in ("pg_advisory", " for update", " for no key update", ";")
        )
    )
