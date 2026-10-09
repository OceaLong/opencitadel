"""Real E11-to-export snapshot promotion gates, collect-only without PostgreSQL."""

# ruff: noqa: F401,F811
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.execution.test_postgres_execution_view import write
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup
from tests.app.infrastructure.repositories.test_execution_export_capture import (
    authorized_read,
    authorized_write,
    repository,
)
from tests.app.infrastructure.repositories.test_execution_export_lifecycle import (
    authorized_kernel_write,
)

pytestmark = pytest.mark.asyncio


async def snapshot_fixture(budget_binding_fixture):
    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    await service.append_score(scope, principal, candidate.result_id, 0, "export-human", payload)
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        snapshot = await work.evaluation_summary.capture(
            scope,
            principal,
            batch.id,
            source="human",
            dimension="correctness",
            rubric_id=payload.rubric_version,
            evaluation_revision=1,
        )
        await work.commit()
    request = {
        "source_kind": "batch",
        "request_id": str(uuid4()),
        "format": "json",
        "batch_id": str(batch.id),
        "source": "human",
        "dimension": "correctness",
        "rubric_id": str(payload.rubric_version),
        "evaluation_revision": 1,
        "snapshot_id": snapshot["id"],
        "timezone": "UTC",
    }
    return service.suites, scope, principal, snapshot, request


async def test_live_snapshot_promotes_exact_rows_and_usage_cut(
    budget_binding_fixture, isolated_database
):
    service, scope, principal, snapshot, request = await snapshot_fixture(budget_binding_fixture)
    for row in snapshot["rows"]:
        for attempt in row["attempts"]:
            await write(
                attempt["run_id"],
                scope,
                1,
                {
                    "family": "evaluation",
                    "status": "completed",
                    "admitted_at": snapshot["captured_at"],
                    "terminal_at": snapshot["captured_at"],
                },
            )
    repo = repository(service)
    accepted = await repo.accept(scope, principal, request)
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        body = db.scalar(
            text("SELECT body FROM export_source_facts WHERE capture_id=CAST(:id AS uuid)"),
            {"id": accepted["id"]},
        )
        assert body["snapshot"] == snapshot
        rows = (
            db.execute(
                text(
                    "SELECT body FROM export_rows WHERE capture_id=CAST(:id AS uuid) ORDER BY ordinal"
                ),
                {"id": accepted["id"]},
            )
            .scalars()
            .all()
        )
        assert rows == snapshot["rows"]
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0


async def test_unattested_historical_batch_revision_is_refused(budget_binding_fixture):
    service, scope, principal, _snapshot, request = await snapshot_fixture(budget_binding_fixture)
    repo = repository(service)
    with pytest.raises(Exception, match="export_snapshot_unavailable"):
        await repo.accept(scope, principal, {**request, "batch_revision": 2147483647})


async def test_wrong_series_cannot_promote_another_snapshot(budget_binding_fixture):
    service, scope, principal, _snapshot, request = await snapshot_fixture(budget_binding_fixture)
    repo = repository(service)
    with pytest.raises(Exception, match="export_snapshot_unavailable"):
        await repo.accept(scope, principal, {**request, "source": "rule"})


async def test_full_1000_case_five_config_pending_matrix_exports_5000_rows(
    budget_binding_fixture, isolated_database
):
    """Real published versions and durable scheduler materialization, no provider calls."""
    import io
    import json
    from datetime import UTC, datetime

    from app.domain.evaluation.batch import schedule_slots
    from app.domain.evaluation.configuration import ConfigSelection, SuiteDefinition
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    suites, scope, principal, previous, _config, _pair, factory = budget_binding_fixture
    ds = suites.datasets
    draft = await ds.create_draft(
        scope, principal, request_id=str(uuid4()), expected_revision=0, name="Export matrix"
    )
    body = json.dumps(
        {
            "schema_version": 1,
            "cases": [{"case_key": f"case-{i}", "input": "fixed input"} for i in range(1000)],
        }
    ).encode()
    preview = await ds.import_validate(
        scope,
        principal,
        dataset_id=draft.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(body),
        content_type="application/json",
    )
    assert not preview.errors
    await ds.import_apply(
        scope,
        principal,
        dataset_id=draft.id,
        import_id=preview.import_id,
        input_digest=preview.input_digest,
        request_id=str(uuid4()),
        expected_revision=1,
    )
    dataset = await ds.publish(
        scope, principal, dataset_id=draft.id, request_id=str(uuid4()), expected_revision=2
    )
    configs = []
    for index in range(5):
        config_draft = await suites.create(
            scope,
            principal,
            kind="config",
            name=f"Config {index}",
            definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
            request_id=str(uuid4()),
        )
        config = await suites.publish(
            scope,
            principal,
            kind="config",
            entity_id=config_draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )
        configs.append(config.id)
    definition = SuiteDefinition(
        **{key: getattr(previous, key) for key in SuiteDefinition.model_fields}
    ).model_copy(update={"dataset_version": dataset.id, "config_versions": tuple(configs)})
    suite_draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Full matrix",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    suite = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=suite_draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    batch_id = uuid4()
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        await DBEvaluationBatchRepository(work.db_session).submit(
            scope, principal, "start", str(uuid4()), {"suite_version": str(suite.id)}, batch_id
        )
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        batch_repo = DBEvaluationBatchRepository(work.db_session)
        claim = await batch_repo.claim(datetime.now(UTC), lease_seconds=120)
        slots = schedule_slots([case.id for case in dataset.cases], configs, 1, 42)
        await batch_repo.materialize(claim, slots, suite.settings.model_dump(mode="json"))
        await work.commit()
    repo = repository(suites)
    accepted = await repo.accept(
        scope,
        principal,
        {
            "source_kind": "batch",
            "format": "json",
            "request_id": str(uuid4()),
            "batch_id": str(batch_id),
            "source": "human",
            "dimension": "correctness",
            "rubric_id": str(suite.rubric_version),
            "evaluation_revision": 0,
            "timezone": "UTC",
        },
    )
    status = await repo.get(scope, principal, accepted["id"])
    assert status["row_count"] == 5000
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        assert (
            db.scalar(
                text("SELECT count(*) FROM export_rows WHERE capture_id=CAST(:id AS uuid)"),
                {"id": accepted["id"]},
            )
            == 5000
        )
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM export_resources WHERE capture_id=CAST(:id AS uuid) AND NOT required_run"
                ),
                {"id": accepted["id"]},
            )
            == 1
        )
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0

    # Admit the exact preallocated RunID after the fixed pending cut.
    with authorized_read(engine, scope, principal) as db:
        pending_run = db.scalar(
            text(
                "SELECT a.run_id FROM evaluation_batch_attempts a JOIN evaluation_batch_results r ON r.scope_key=a.scope_key AND r.id=a.result_id WHERE r.batch_id=CAST(:batch AS uuid) AND r.ordinal=0 AND a.attempt=0"
            ),
            {"batch": str(batch_id)},
        )
        assert pending_run is not None
        assert not db.scalar(
            text("SELECT EXISTS(SELECT 1 FROM execution_view_runs WHERE run_id=:run)"),
            {"run": pending_run},
        )
    await write(
        pending_run,
        scope,
        1,
        {"family": "evaluation", "status": "running", "admitted_at": datetime.now(UTC).isoformat()},
    )
    with authorized_read(engine, scope, principal) as db:
        assert db.scalar(
            text("SELECT EXISTS(SELECT 1 FROM execution_view_runs WHERE run_id=:run)"),
            {"run": pending_run},
        )
    # Admission/progress after acceptance cannot replace the fixed pending row.
    with authorized_kernel_write(engine) as db:
        changed = db.execute(
            text(
                "UPDATE evaluation_batch_results SET execution_status='running',revision=revision+1 WHERE batch_id=CAST(:batch AS uuid) AND ordinal=0"
            ),
            {"batch": str(batch_id)},
        )
        assert changed.rowcount == 1
    assert (await repo.get(scope, principal, accepted["id"]))["status"] == "queued"
    with authorized_read(engine, scope, principal) as db:
        assert (
            db.scalar(
                text(
                    "SELECT body->>'execution_status' FROM export_rows WHERE capture_id=CAST(:id AS uuid) AND ordinal=0"
                ),
                {"id": accepted["id"]},
            )
            == "queued"
        )

    from app.application.services.execution_export_download import ExportDownloader
    from app.application.services.execution_export_worker import ExecutionExportWorker
    from tests.app.infrastructure.repositories.test_execution_export_lifecycle import (
        PrivateObjects,
        kernel_repository,
    )

    objects = PrivateObjects()
    async with kernel_repository(repo) as kernel:
        await ExecutionExportWorker(kernel, objects).process_pending()
    assert (await repo.get(scope, principal, accepted["id"]))["status"] == "ready"
    spool, _, _ = await ExportDownloader(repo, objects).prepare(scope, principal, accepted["id"])
    try:
        exported = json.load(spool)
        assert len(exported["rows"]) == 5000
        assert len({row["case_id"] for row in exported["rows"]}) == 1000
        assert len({row["config_id"] for row in exported["rows"]}) == 5
        assert all(row["execution_status"] == "queued" for row in exported["rows"])
    finally:
        spool.close()


async def test_corrupt_persisted_snapshot_5001_rows_is_rejected_without_export_effects(
    budget_binding_fixture, isolated_database
):
    import json

    service, scope, principal, snapshot, payload = await snapshot_fixture(budget_binding_fixture)
    engine, _ = isolated_database
    # E11 captures at most 5000 rows. Create a corrupt persisted snapshot only
    # in this isolated database to exercise the export's defensive size check.
    oversized = {**snapshot, "rows": [snapshot["rows"][0]] * 5001}
    with authorized_write(engine, scope, principal) as db:
        db.execute(
            text("ALTER TABLE evaluation_summary_snapshots DISABLE TRIGGER e11_snapshot_immutable")
        )
        changed = db.execute(
            text(
                "UPDATE evaluation_summary_snapshots SET body=CAST(:body AS jsonb) WHERE id=CAST(:id AS uuid)"
            ),
            {"body": json.dumps(oversized, default=str), "id": snapshot["id"]},
        )
        assert changed.rowcount == 1
        db.execute(
            text("ALTER TABLE evaluation_summary_snapshots ENABLE TRIGGER e11_snapshot_immutable")
        )
    with pytest.raises(ValueError, match="export_capacity_exceeded"):
        await repository(service).accept(scope, principal, payload)
    with authorized_read(engine, scope, principal) as db:
        assert db.scalar(text("SELECT count(*) FROM execution_exports")) == 0
        assert db.scalar(text("SELECT count(*) FROM export_receipts")) == 0
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0
