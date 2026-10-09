# ruff: noqa: F811 -- pytest fixture imports intentionally share parameter names
"""Strict owned PostgreSQL integration; never initialize the shared database."""

import asyncio
import io
import json
import os
from uuid import uuid4

import pytest

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture
async def datasets(isolated_database):
    from app.application.evaluation.dataset_service import DatasetService
    from app.domain.models.scope import OwnerScope, Principal
    from app.infrastructure.repositories.db_evaluation_dataset_repository import (
        DatasetObjectLifecycle,
    )
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from app.infrastructure.storage.postgres import Postgres
    from core.config import load_deployment_settings
    from tests.app.application.services.test_artifact_provenance_postgres import Objects, seed

    owner, _ = await seed()
    settings = load_deployment_settings()
    engine, _ = isolated_database
    resource = Postgres(
        settings.model_copy(
            update={
                "sqlalchemy_database_uri": engine.url.set(
                    drivername="postgresql+asyncpg",
                    username=os.environ["POSTGRES_USER"],
                    password=os.environ["POSTGRES_PASSWORD"],
                ).render_as_string(hide_password=False),
                "postgres_pool_size": 2,
                "postgres_max_overflow": 0,
                "env": "test",
            }
        )
    )
    await resource.init()

    def uow(authorization_context=None):
        return DBUnitOfWork(
            resource.session_factory,
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=authorization_context,
        )

    from sqlalchemy import text

    async with resource.session_factory() as db:
        assert await db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        )
        assert await db.scalar(
            text(
                "SELECT relowner <> (SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_class WHERE oid='evaluation_datasets'::regclass"
            )
        )
    objects = Objects()
    lifecycle = DatasetObjectLifecycle(
        resource.upload_intent_session_factory,
        objects,
        signing_secret=settings.database_authorization_signing_secret,
    )
    service = DatasetService(uow, objects, lifecycle, cursor_secret=b"e10-owned-test-cursor-secret")
    try:
        yield service, OwnerScope.personal(owner), Principal(user_id=owner), objects, lifecycle
    finally:
        await resource.shutdown()


async def draft(service, scope, principal):
    return await service.create_draft(
        scope, principal, request_id=str(uuid4()), expected_revision=0, name="Test"
    )


async def test_concurrent_revision_and_request_replay_are_atomic(datasets):
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.errors import DatasetConflict

    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    request = str(uuid4())

    async def edit(key, request_id):
        return await service.update_case(
            scope,
            principal,
            dataset_id=initial.id,
            request_id=request_id,
            expected_revision=1,
            case=CaseRevision(case_key=key, input="x"),
        )

    results = await asyncio.gather(
        edit("a", request), edit("b", str(uuid4())), return_exceptions=True
    )
    assert sum(isinstance(r, DatasetConflict) for r in results) == 1
    winner = next(r for r in results if not isinstance(r, Exception))
    assert winner.revision == 2
    assert len((await service.get_draft(scope, principal, initial.id)).cases) == 1


async def test_import_apply_and_fixed_version_survive_later_draft_edit(datasets):
    from app.domain.evaluation.dataset import CaseRevision

    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    data = json.dumps(
        {"schema_version": 1, "cases": [{"case_key": "a", "input": "original", "tags": ["tag"]}]}
    ).encode()
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(data),
        content_type="application/json",
    )
    assert not preview.errors
    request = str(uuid4())
    applied = await service.import_apply(
        scope,
        principal,
        dataset_id=initial.id,
        import_id=preview.import_id,
        input_digest=preview.input_digest,
        request_id=request,
        expected_revision=1,
    )
    replay = await service.import_apply(
        scope,
        principal,
        dataset_id=initial.id,
        import_id=preview.import_id,
        input_digest=preview.input_digest,
        request_id=request,
        expected_revision=1,
    )
    assert replay == applied
    version = await service.publish(
        scope, principal, dataset_id=initial.id, request_id=str(uuid4()), expected_revision=2
    )
    await service.update_case(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=3,
        case=CaseRevision(case_key="a", input="new"),
    )
    fixed = await service.get_version(scope, principal, version.id)
    assert fixed.cases[0].input == "original"
    assert fixed.cases[0].tags == ("tag",)


async def test_invalid_999th_row_never_changes_draft(datasets):
    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    cases = [{"case_key": str(i), "input": "x"} for i in range(1000)]
    cases[998]["case_key"] = "1"
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(json.dumps({"schema_version": 1, "cases": cases}).encode()),
        content_type="application/json",
    )
    assert any(e.row == 999 for e in preview.errors)
    assert (await service.get_draft(scope, principal, initial.id)).revision == 1
    assert not (await service.get_draft(scope, principal, initial.id)).cases


async def test_same_request_replays_once_and_conflicting_intent_fails(datasets):
    from sqlalchemy import text

    from app.domain.evaluation.errors import DatasetConflict

    service, scope, principal, _, _ = datasets
    request = str(uuid4())

    async def create():
        return await service.create_draft(
            scope, principal, request_id=request, expected_revision=0, name="one"
        )

    first, second = await asyncio.gather(create(), create())
    assert first == second
    with pytest.raises(DatasetConflict, match="request_conflict"):
        await service.create_draft(
            scope, principal, request_id=request, expected_revision=0, name="other"
        )
    from tests.app.execution_test_support import execution_admin_session

    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM audit_logs WHERE request_id=:request"),
                {"request": request},
            )
            == 1
        )
        receipt = await db.scalar(
            text("SELECT result FROM evaluation_mutations WHERE request_id=:request"),
            {"request": request},
        )
        assert "cases" not in receipt


@pytest.mark.parametrize("cancel", [False, True])
async def test_audit_failure_restores_scope_and_rolls_back_whole_command(
    datasets, monkeypatch, cancel
):
    from sqlalchemy import text

    from app.domain.evaluation.dataset import CaseRevision
    from app.infrastructure.repositories.db_audit_repository import DBAuditRepository
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    original_add, original_rollback = DBAuditRepository.add, DBUnitOfWork.rollback
    restored = []

    async def fail(self, log):
        await original_add(self, log)
        raise asyncio.CancelledError() if cancel else RuntimeError("audit unavailable")

    async def rollback(self):
        if self.db_session and self.db_session.is_active:
            restored.append(
                await self.db_session.scalar(text("SELECT current_setting('app.auth_mode')"))
            )
        return await original_rollback(self)

    monkeypatch.setattr(DBAuditRepository, "add", fail)
    monkeypatch.setattr(DBUnitOfWork, "rollback", rollback)
    request = str(uuid4())
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await service.update_case(
            scope,
            principal,
            dataset_id=initial.id,
            request_id=request,
            expected_revision=1,
            case=CaseRevision(case_key="a", input="private"),
        )
    monkeypatch.setattr(DBAuditRepository, "add", original_add)
    assert "user" in restored
    current = await service.get_draft(scope, principal, initial.id)
    assert current.revision == 1
    assert not current.cases
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM evaluation_mutations WHERE request_id=:request"),
                {"request": request},
            )
            == 0
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM audit_logs WHERE request_id=:request"),
                {"request": request},
            )
            == 0
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM evaluation_object_intents WHERE dataset_id=:id AND cleaned_at IS NULL"
                ),
                {"id": initial.id},
            )
            == 1
        )


async def test_scope_revocation_auditor_and_cross_workspace_fail_closed(datasets):
    from sqlalchemy import text

    from app.domain.evaluation.errors import DatasetUnavailable
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.user import GlobalRole
    from tests.app.application.services.test_artifact_provenance_postgres import seed
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    other, _ = await seed()
    with pytest.raises(DatasetUnavailable):
        await service.get_draft(OwnerScope.personal(other), Principal(user_id=other), initial.id)
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='auditor' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    auditor = Principal(user_id=principal.user_id, global_role=GlobalRole.AUDITOR)
    assert (await service.get_draft(scope, auditor, initial.id)).id == initial.id
    with pytest.raises(PermissionError):
        await service.create_draft(
            scope, auditor, request_id=str(uuid4()), expected_revision=0, name="denied"
        )
    with pytest.raises(PermissionError):
        await service.get_draft(scope, principal, initial.id)


async def test_publish_pins_fixed_file_and_force_delete_invalidates_version(datasets):
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.models.file import File
    from app.domain.models.resource_pin import ResourcePinned, ResourceUnavailable

    service, scope, principal, _, _ = datasets
    file = File(
        owner_user_id=scope.user_id,
        content_digest="a" * 64,
        object_identity=str(uuid4()),
        key="fixed-file",
    )
    async with service.uow_factory(service._auth(scope, principal)) as uow:
        await uow.file.save(file)
        await uow.commit()
    initial = await draft(service, scope, principal)
    await service.update_case(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(case_key="a", input="x", attachments=[file.id]),
    )
    version = await service.publish(
        scope, principal, dataset_id=initial.id, request_id=str(uuid4()), expected_revision=2
    )
    assert len(version.pins) == 1
    async with service.uow_factory(service._auth(scope, principal)) as uow:
        with pytest.raises(ResourcePinned):
            await uow.file.prepare_delete(file.id, scope=scope)
    async with service.uow_factory(service._auth(scope, principal)) as uow:
        await uow.file.prepare_delete(file.id, scope=scope, force=True)
        await uow.file.delete(file.id, scope=scope)
        await uow.commit()
    with pytest.raises(ResourceUnavailable):
        await service.get_version(scope, principal, version.id)


async def test_failed_upload_is_durable_and_cleanup_does_not_race_live_writer(datasets):
    from datetime import UTC, datetime, timedelta

    from app.domain.evaluation.dataset import CaseRevision

    service, scope, principal, objects, lifecycle = datasets
    initial = await draft(service, scope, principal)
    entered, release = asyncio.Event(), asyncio.Event()
    original_put = objects.put_bytes

    async def failing(key, data):
        await original_put(key, data)
        entered.set()
        await release.wait()
        raise OSError("upload interrupted")

    objects.put_bytes = failing
    task = asyncio.create_task(
        service.update_case(
            scope,
            principal,
            dataset_id=initial.id,
            request_id=str(uuid4()),
            expected_revision=1,
            case=CaseRevision(case_key="a", input="x"),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await lifecycle.cleanup(now=datetime.now(UTC) + timedelta(days=2)) == 0
        assert objects.data
    finally:
        release.set()
    with pytest.raises(OSError, match="upload interrupted"):
        await task
    assert await lifecycle.cleanup(now=datetime.now(UTC) + timedelta(days=2)) == 1
    assert not objects.data
    assert (await service.get_draft(scope, principal, initial.id)).revision == 1


async def test_expired_import_cleanup_keeps_manifest_referenced_by_immutable_cases(datasets):
    from datetime import UTC, datetime, timedelta

    service, scope, principal, objects, lifecycle = datasets
    initial = await draft(service, scope, principal)
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(b'{"schema_version":1,"cases":[{"case_key":"a","input":"x"}]}'),
        content_type="application/json",
    )
    assert await lifecycle.cleanup(now=datetime.now(UTC) + timedelta(hours=2)) == 0
    await service.import_apply(
        scope,
        principal,
        dataset_id=initial.id,
        import_id=preview.import_id,
        input_digest=preview.input_digest,
        request_id=str(uuid4()),
        expected_revision=1,
    )
    assert await lifecycle.cleanup(now=datetime.now(UTC) + timedelta(days=2)) == 0
    assert objects.data


async def test_thousand_case_apply_has_one_manifest_and_bulk_read_once(datasets):
    service, scope, principal, objects, _ = datasets
    initial = await draft(service, scope, principal)
    raw = json.dumps(
        {
            "schema_version": 1,
            "cases": [{"case_key": str(i), "input": "question"} for i in range(1000)],
        }
    ).encode()
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(raw),
        content_type="application/json",
    )
    assert not preview.errors
    assert len(objects.data) == 1
    original_get, reads = objects.get_bytes, []

    async def get(key):
        reads.append(key)
        return await original_get(key)

    objects.get_bytes = get
    applied = await service.import_apply(
        scope,
        principal,
        dataset_id=initial.id,
        import_id=preview.import_id,
        input_digest=preview.input_digest,
        request_id=str(uuid4()),
        expected_revision=1,
    )
    assert len(applied.cases) == 1000
    assert len(reads) == 1
    reads.clear()
    assert len((await service.get_draft(scope, principal, initial.id)).cases) == 1000
    assert len(reads) == 1
    summary = (await service.list_drafts(scope, principal))[0]
    assert summary.case_count == 1000
    assert summary.version_count == 0


async def test_apply_digest_tampering_is_atomic_and_version_owner_must_exist(datasets):
    from app.domain.evaluation.errors import DatasetConflict, DatasetUnavailable
    from app.domain.models.resource_pin import ResourceUnavailable

    service, scope, principal, objects, _ = datasets
    initial = await draft(service, scope, principal)
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(b'{"schema_version":1,"cases":[{"case_key":"a","input":"x"}]}'),
        content_type="application/json",
    )
    with pytest.raises(DatasetConflict):
        await service.import_apply(
            scope,
            principal,
            dataset_id=initial.id,
            import_id=preview.import_id,
            input_digest="0" * 64,
            request_id=str(uuid4()),
            expected_revision=1,
        )
    key = next(iter(objects.data))
    objects.data[key] = objects.data[key].replace(b'"x"', b'"tampered"')
    with pytest.raises(DatasetUnavailable):
        await service.import_apply(
            scope,
            principal,
            dataset_id=initial.id,
            import_id=preview.import_id,
            input_digest=preview.input_digest,
            request_id=str(uuid4()),
            expected_revision=1,
        )
    assert (await service.get_draft(scope, principal, initial.id)).revision == 1
    async with service.uow_factory(service._auth(scope, principal)) as uow:
        with pytest.raises(ResourceUnavailable, match="owner"):
            await uow.resource_pins.acquire(scope, "dataset_version", str(uuid4()), ())


async def test_csv_foreign_attachment_error_has_exact_physical_row_and_field(datasets):
    import csv

    from app.application.evaluation.import_parser import CSV_COLUMNS

    service, scope, principal, _, _ = datasets
    initial = await draft(service, scope, principal)
    body = io.StringIO(newline="")
    writer = csv.writer(body)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(["a", "line one\nline two", "", "[]", "[]", "[]", "[]"])
    writer.writerow(["b", "input", "", "[]", "[]", '["foreign-file"]', "[]"])
    preview = await service.import_validate(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        stream=io.BytesIO(body.getvalue().encode()),
        content_type="text/csv",
    )
    assert [(error.row, error.field, error.code) for error in preview.errors] == [
        (4, "attachments.0", "resource_unavailable")
    ]


async def test_cleanup_full_retained_page_cannot_starve_later_orphan(datasets):
    from datetime import UTC, datetime, timedelta

    from app.domain.evaluation.dataset import CaseRevision

    service, scope, principal, objects, lifecycle = datasets
    initial = await draft(service, scope, principal)
    for index in range(100):
        await service.update_case(
            scope,
            principal,
            dataset_id=initial.id,
            request_id=str(uuid4()),
            expected_revision=index + 1,
            case=CaseRevision(case_key="retained", input=str(index)),
        )
    retained = set(objects.data)
    put = objects.put_bytes

    async def fail_after_put(key, data):
        await put(key, data)
        raise OSError("abandoned")

    objects.put_bytes = fail_after_put
    with pytest.raises(OSError, match="abandoned"):
        await service.update_case(
            scope,
            principal,
            dataset_id=initial.id,
            request_id=str(uuid4()),
            expected_revision=101,
            case=CaseRevision(case_key="orphan", input="orphan"),
        )
    for _ in range(2):
        await lifecycle.cleanup(now=datetime.now(UTC) + timedelta(days=2))
    assert set(objects.data) == retained


async def test_e10_fixed_history_is_scoped_paginated_and_read_only(datasets):
    from app.domain.evaluation.dataset import CaseRevision

    service, scope, principal, _objects, _ = datasets
    initial = await draft(service, scope, principal)
    current = await service.update_case(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(case_key="a", input="original"),
    )
    first = await service.publish(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=current.revision,
    )
    current = await service.get_draft(scope, principal, initial.id)
    second = await service.publish(
        scope,
        principal,
        dataset_id=initial.id,
        request_id=str(uuid4()),
        expected_revision=current.revision,
    )
    page = await service.list_versions(scope, principal, initial.id, limit=1)
    assert [v.id for v in page.items] == [second.id]
    assert page.next_cursor
    next_page = await service.list_versions(
        scope, principal, initial.id, cursor=page.next_cursor, limit=1
    )
    assert [v.id for v in next_page.items] == [first.id]
    assert next_page.next_cursor is None
    assert (await service.get_draft(scope, principal, initial.id)).revision == current.revision + 1
    with pytest.raises(ValueError, match="cursor"):
        await service.list_versions(scope, principal, initial.id, cursor="tampered", limit=1)
    other = await draft(service, scope, principal)
    with pytest.raises(ValueError, match="cursor"):
        await service.list_versions(scope, principal, other.id, cursor=page.next_cursor, limit=1)
