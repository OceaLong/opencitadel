"""Real UOW/session/repository dispatch with only driver effects injected."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget, EvidenceQuotaError
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.observer_session import ObserverSession
from sqlalchemy import create_engine, literal, select
from sqlalchemy.engine import Connection, IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.uow import DBUnitOfWorkDependencies, create_uow_factory
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.original_evidence import _EVIDENCE_KEY
from app.infrastructure.security.db_authorization import _AUTHORIZATION_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "family", ["configuration", "dataset", "environment", "batch", "results", "counts"]
)
@pytest.mark.parametrize("oversize", [False, True])
async def test_actual_nested_uow_repository_retains_before_conversion(
    monkeypatch, family, oversize
):
    engine = create_engine("sqlite://")
    owner = EvidenceOwner(
        budget=EvidenceBudget(bytes_limit=1024 * 1024, rows_limit=10000, row_limit=1024)
    )
    identity = UUID(int=1)
    raw = {"opaque": "original", "id": identity}
    calls = []
    original = Connection.execute

    def result(columns, rows):
        return IteratorResult(SimpleResultMetaData(columns), iter(rows))

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement is _AUTHORIZATION_SQL:
            calls.append("authorization")
            return result(["value"], [("fixture",)])
        if statement.get_execution_options().get("c2c_preflight"):
            calls.append("preflight")
            two_rows = family in {"dataset", "counts"} and (
                "evaluation_version_cases" in str(statement)
                or "SELECT execution_status,count(*)" in str(statement)
            )
            return original(
                connection,
                select(
                    *[
                        literal(value).label(name)
                        for name, value in {
                            "row_count": 2 if two_rows else 1,
                            "max_bytes": 5000 if oversize else 40,
                            "total_bytes": 5000 if oversize else 80 if two_rows else 40,
                            "read_only": "on",
                            "isolation": "repeatable read",
                            "snapshot": "fixture:1",
                        }.items()
                    ]
                ),
            )
        calls.append("original")
        sql = str(statement)
        if "SELECT body FROM evaluation_" in sql:
            return result(["body"], [(raw,)])
        if "SELECT id,dataset_id,revision" in sql:
            return result(["id", "dataset_id", "revision"], [(identity, identity, 1)])
        if "SELECT c.*" in sql:
            return result(["case_key", "original"], [("case", raw), ("case-2", raw)])
        if "SELECT revision,body,digest" in sql:
            from app.domain.evaluation.configuration import digest

            body = {
                "id": str(identity),
                "physical_resource": "fixture",
                "kind": "http",
                "endpoint": "https://fixture.invalid",
            }
            return result(["revision", "body", "digest"], [(1, body, digest(body))])
        if "SELECT execution_status,count(*)" in sql:
            return result(["execution_status", "count"], [("completed", 1), ("failed", 1)])
        return result(["id", "original"], [(identity, raw)])

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    factory = create_uow_factory(
        session_factory=lambda: AsyncSession(sync_session_class=Bound),
        dependencies=DBUnitOfWorkDependencies(
            secret_cipher=object(),
            audit_signing_key="fixture-only",
            audit_signing_key_id="fixture",
            database_authorization_signing_secret="fixture-only",
        ),
    )
    scope = OwnerScope.personal("fixture-user")

    async def read():
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            if family == "configuration":
                return await work.evaluation_configuration.get_version(scope, "suite", identity)
            if family == "dataset":
                return await work.evaluation_dataset.get_version(scope, identity)
            if family == "environment":
                return await work.evaluation_environment.registered(scope, "target", identity)
            if family == "batch":
                return await work.evaluation_batch.get(scope, identity)
            if family == "results":
                return await work.evaluation_batch.results(scope, identity)
            return await work.evaluation_batch.counts(scope, identity)

    if oversize:
        with pytest.raises(EvidenceQuotaError):
            await read()
        assert calls == ["authorization", "preflight"]
    else:
        assert await read()
        assert calls == ["authorization"] + ["preflight", "original"] * (
            2 if family == "dataset" else 1
        )
        retained = owner.originals[
            "version-source"
            if family in {"configuration", "dataset", "environment"}
            else "batch-source"
        ]
        assert retained
        if family == "dataset":
            members = [row for row in retained if row["operation"] == "dataset.members"]
            assert len(members) == 2
            assert [row["read"]["sql_index"] for row in members] == [1, 1]
        if family == "counts":
            assert retained[0]["read"]["sql_index"] == 0
            assert len(retained[0]["value"]) == 2
        if family == "configuration":
            assert retained[0]["value"] == raw
            raw["opaque"] = "mutated after original"
            assert retained[0]["value"]["opaque"] == "original"
    engine.dispose()


@pytest.mark.asyncio
async def test_batch_result_rows_keep_one_sql_owner_and_reject_intervening_dispatch(monkeypatch):
    import asyncio

    from sqlalchemy import column, table

    from app.infrastructure.repositories import db_evaluation_batch_repository as repository_module

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(budget=EvidenceBudget())
    returned = []
    row_mode = "normal"
    original = Connection.execute

    def result(columns, rows):
        item = IteratorResult(SimpleResultMetaData(columns), iter(rows))
        returned.append(item)
        return item

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            return original(
                connection,
                select(
                    literal(2).label("row_count"),
                    literal(40).label("max_bytes"),
                    literal(80).label("total_bytes"),
                    literal("on").label("read_only"),
                    literal("repeatable read").label("isolation"),
                    literal("fixture:1").label("snapshot"),
                ),
            )
        if "evaluation_batch_results r" in str(statement):
            if row_mode == "empty":
                return result(["id", "ordinal"], [])
            if row_mode == "cancel":

                def cancelled_rows():
                    yield ("first", 1)
                    raise asyncio.CancelledError()

                return result(["id", "ordinal"], cancelled_rows())
            return result(["id", "ordinal"], [("first", 1), ("second", 2)])
        if "SELECT execution_status,count(*)" in str(statement):
            return result(["execution_status", "count"], [("completed", 1), ("failed", 1)])
        return result(["id"], [("intervening",)])

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    scope = OwnerScope.personal("fixture-user")
    async with AsyncSession(sync_session_class=Bound) as session:
        repository = repository_module.DBEvaluationBatchRepository(session)
        assert [row["id"] for row in await repository.results(scope, UUID(int=1))] == [
            "first",
            "second",
        ]
        reads = owner.originals["batch-source"]
        assert [row["read"]["sql_index"] for row in reads] == [0, 0]
        assert returned[-1].closed
        assert session.sync_session._evidence_latest_result is None

        original_retain = repository_module.retain_read

        def interleave(*args, **kwargs):
            original_retain(*args, **kwargs)
            if kwargs.get("source_result") is not None and args[4]["ordinal"] == 1:
                extra = session.sync_session.execute(
                    select(table("execution_run_projection", column("id")))
                )
                extra.close()

        monkeypatch.setattr(repository_module, "retain_read", interleave)
        with pytest.raises(ValueError, match="result missing or superseded"):
            await repository.results(scope, UUID(int=1))
        assert [row["read"]["sql_index"] for row in owner.originals["batch-source"]] == [
            0,
            0,
            1,
        ]
        assert returned[-2].closed
        monkeypatch.setattr(repository_module, "retain_read", original_retain)
        row_mode = "cancel"
        with pytest.raises(asyncio.CancelledError):
            await repository.results(scope, UUID(int=1))
        assert returned[-1].closed
        assert session.sync_session._evidence_latest_result is None
        assert [row["value"]["id"] for row in owner.originals["batch-source"]] == [
            "first",
            "second",
            "first",
            "first",
        ]
        row_mode = "empty"
        assert await repository.results(scope, UUID(int=1)) == []
        assert owner.originals["batch-source"][-1]["value"] == []
        assert owner.originals["batch-source"][-1]["read"]["sql_index"] == 4
        assert returned[-1].closed

        prior_reads = len(owner.originals["batch-source"])

        def before_counts(*args, **kwargs):
            if args[2] == "batch.counts":
                extra = session.sync_session.execute(
                    select(table("execution_run_projection", column("id")))
                )
                extra.close()
            return original_retain(*args, **kwargs)

        monkeypatch.setattr(repository_module, "retain_read", before_counts)
        with pytest.raises(ValueError, match="result missing or superseded"):
            await repository.counts(scope, UUID(int=1))
        assert len(owner.originals["batch-source"]) == prior_reads
        assert returned[-2].closed
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["authorize", "dataset", "environment", "batch"])
async def test_single_row_original_result_rejects_intervening_dispatch(monkeypatch, family):
    from sqlalchemy import column, table

    from app.infrastructure.repositories import (
        db_evaluation_batch_repository,
        db_evaluation_dataset_repository,
        db_evaluation_environment_repository,
    )

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(budget=EvidenceBudget())
    returned = []
    original = Connection.execute

    def result(columns, rows):
        item = IteratorResult(SimpleResultMetaData(columns), iter(rows))
        returned.append(item)
        return item

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            return original(
                connection,
                select(
                    literal(1).label("row_count"),
                    literal(40).label("max_bytes"),
                    literal(40).label("total_bytes"),
                    literal("on").label("read_only"),
                    literal("repeatable read").label("isolation"),
                    literal("fixture:1").label("snapshot"),
                ),
            )
        sql = str(statement)
        if "SELECT status,token_version,global_role" in sql:
            return result(["status", "token_version", "global_role"], [("active", 1, "member")])
        if "SELECT id,dataset_id,revision" in sql:
            return result(["id", "dataset_id", "revision"], [(UUID(int=1), UUID(int=1), 1)])
        if "SELECT revision,body,digest" in sql:
            return result(["revision", "body", "digest"], [(1, {}, "0" * 64)])
        if "SELECT * FROM evaluation_batches" in sql:
            return result(["id"], [(UUID(int=1),)])
        return result(["id"], [("intervening",)])

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    modules = {
        "authorize": db_evaluation_dataset_repository,
        "dataset": db_evaluation_dataset_repository,
        "environment": db_evaluation_environment_repository,
        "batch": db_evaluation_batch_repository,
    }
    module = modules[family]
    operation = {
        "authorize": "dataset.authorize",
        "dataset": "dataset.get_version",
        "environment": "environment.registered",
        "batch": "batch.get",
    }[family]
    original_retain = module.retain_read
    captured = []
    scope = OwnerScope.personal("fixture-user")
    async with AsyncSession(sync_session_class=Bound) as session:

        def interleave(*args, **kwargs):
            if args[2] == operation:
                captured.append(kwargs["source_result"])
                extra = session.sync_session.execute(
                    select(table("execution_run_projection", column("id")))
                )
                extra.close()
            return original_retain(*args, **kwargs)

        monkeypatch.setattr(module, "retain_read", interleave)

        async def read():
            if family == "authorize":
                principal = SimpleNamespace(
                    user_id=scope.user_id,
                    is_auditor=False,
                    token_version=1,
                    global_role="member",
                    team_roles={},
                )
                await db_evaluation_dataset_repository.DBEvaluationDatasetRepository(
                    session
                ).authorize(scope, principal, write=False)
            elif family == "dataset":
                await db_evaluation_dataset_repository.DBEvaluationDatasetRepository(
                    session
                ).get_version(scope, UUID(int=1))
            elif family == "environment":
                await db_evaluation_environment_repository.DBEvaluationEnvironmentRepository(
                    session
                ).registered(scope, "target", UUID(int=1))
            else:
                await db_evaluation_batch_repository.DBEvaluationBatchRepository(session).get(
                    scope, UUID(int=1)
                )

        with pytest.raises(ValueError, match="result missing or superseded"):
            await read()
        assert len(captured) == 1
        assert captured[0].closed
        assert captured[0] is returned[-2]
        assert not owner.originals.get(
            "principal-source"
            if family == "authorize"
            else "batch-source"
            if family == "batch"
            else "version-source"
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_empty_batch_result_is_retained_before_missing_error(monkeypatch):
    from app.domain.evaluation.errors import DatasetNotFound
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(budget=EvidenceBudget())
    returned = []
    original = Connection.execute

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            return original(
                connection,
                select(
                    literal(0).label("row_count"),
                    literal(0).label("max_bytes"),
                    literal(0).label("total_bytes"),
                    literal("on").label("read_only"),
                    literal("repeatable read").label("isolation"),
                    literal("fixture:1").label("snapshot"),
                ),
            )
        item = IteratorResult(SimpleResultMetaData(["id"]), iter(()))
        returned.append(item)
        return item

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    async with AsyncSession(sync_session_class=Bound) as session:
        with pytest.raises(DatasetNotFound, match="batch_unavailable"):
            await DBEvaluationBatchRepository(session).get(
                OwnerScope.personal("fixture-user"), UUID(int=1)
            )
        retained = owner.originals["batch-source"]
        assert len(retained) == 1
        assert retained[0]["operation"] == "batch.get"
        assert retained[0]["value"] is None
        assert returned[0].closed
        assert session.sync_session._evidence_latest_result is None
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["configuration", "team_role"])
@pytest.mark.parametrize("mode", ["normal", "missing", "interleave"])
async def test_scalar_convenience_read_binds_original_result(monkeypatch, family, mode):
    from sqlalchemy import column, table

    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.models.scope import Principal
    from app.domain.models.team import TeamRole
    from app.infrastructure.repositories import (
        db_evaluation_configuration_repository,
        db_evaluation_dataset_repository,
    )

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(budget=EvidenceBudget())
    original = Connection.execute
    returned = []
    original_count = 0
    body = {"opaque": "original"}

    def result(columns, rows):
        item = IteratorResult(SimpleResultMetaData(columns), iter(rows))
        returned.append(item)
        return item

    def driver(connection, statement, parameters=None, *args, **kwargs):
        nonlocal original_count
        if statement.get_execution_options().get("c2c_preflight"):
            absent = mode == "missing" and (family == "configuration" or original_count >= 1)
            return original(
                connection,
                select(
                    literal(0 if absent else 1).label("row_count"),
                    literal(0 if absent else 40).label("max_bytes"),
                    literal(0 if absent else 40).label("total_bytes"),
                    literal("on").label("read_only"),
                    literal("repeatable read").label("isolation"),
                    literal("fixture:scalar").label("snapshot"),
                ),
            )
        original_count += 1
        sql = str(statement)
        if "SELECT body FROM evaluation_" in sql:
            return result(["body"], [] if mode == "missing" else [(body,)])
        if "SELECT status,token_version,global_role" in sql:
            return result(["status", "token_version", "global_role"], [("active", 0, "user")])
        if "SELECT role FROM team_members" in sql:
            return result(["role"], [] if mode == "missing" else [("member",)])
        return result(["id"], [("intervening",)])

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    module = (
        db_evaluation_configuration_repository
        if family == "configuration"
        else db_evaluation_dataset_repository
    )
    operation = "configuration.get_version" if family == "configuration" else "dataset.team_role"
    original_retain = module.retain_read
    captured = []
    scope = (
        OwnerScope.personal("fixture-user")
        if family == "configuration"
        else OwnerScope.team("fixture-user", "fixture-team")
    )
    principal = Principal(user_id="fixture-user", team_roles={"fixture-team": TeamRole.MEMBER})
    async with AsyncSession(sync_session_class=Bound) as session:

        def retain_with_identity(*args, **kwargs):
            if args[2] == operation:
                source = kwargs["source_result"]
                assert source is session.sync_session._evidence_latest_result[0]
                captured.append(source)
                if mode == "interleave":
                    extra = session.sync_session.execute(
                        select(table("execution_run_projection", column("id")))
                    )
                    extra.close()
            return original_retain(*args, **kwargs)

        monkeypatch.setattr(module, "retain_read", retain_with_identity)

        async def read():
            if family == "configuration":
                return await module.DBEvaluationConfigurationRepository(session).get_version(
                    scope, "suite", UUID(int=1)
                )
            return await module.DBEvaluationDatasetRepository(session).authorize(
                scope, principal, write=False
            )

        if mode == "interleave":
            with pytest.raises(ValueError, match="result missing or superseded"):
                await read()
        elif mode == "missing":
            expected = DatasetNotFound if family == "configuration" else PermissionError
            with pytest.raises(expected):
                await read()
        else:
            assert await read() == (body if family == "configuration" else None)
        assert len(captured) == 1
        assert captured[0].closed
        retained = owner.originals.get(
            "version-source" if family == "configuration" else "principal-source", []
        )
        if family == "team_role":
            assert retained[0]["operation"] == "dataset.authorize"
            assert retained[0]["read"]["sql_index"] == 0
        own = [row for row in retained if row["operation"] == operation]
        if mode == "interleave":
            assert not own
        else:
            assert len(own) == 1
            assert own[0]["value"] == (
                None if mode == "missing" else body if family == "configuration" else "member"
            )
            assert own[0]["read"]["sql_index"] == (0 if family == "configuration" else 1)
        if family == "configuration" and mode == "normal":
            body["opaque"] = "mutated"
            assert own[0]["value"]["opaque"] == "original"
        if family == "team_role" and mode == "normal":
            prior_queries = original_count
            await module.DBEvaluationDatasetRepository(session).authorize(
                OwnerScope.personal("fixture-user"), principal, write=False
            )
            assert original_count == prior_queries + 1
            roles = [
                row for row in owner.originals["principal-source"] if row["operation"] == operation
            ]
            assert len(roles) == 1
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "replacement"])
async def test_actual_repository_refuses_missing_or_replaced_owner_before_read(mutation):
    from app.infrastructure.repositories.db_evaluation_configuration_repository import (
        DBEvaluationConfigurationRepository,
    )

    owner = EvidenceOwner()
    session = ObserverSession(budget=owner.budget, evidence=owner)
    if mutation == "missing":
        del session.info[_EVIDENCE_KEY]
    else:
        session.info[_EVIDENCE_KEY] = EvidenceOwner()
    with pytest.raises(ValueError, match="owner missing or replaced"):
        await DBEvaluationConfigurationRepository(session).get_version(
            OwnerScope.personal("fixture"), "suite", UUID(int=1)
        )
    session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "interleave"),
    [(family, False) for family in ("file", "principal", "knowledge", "document", "pin_file")]
    + [(family, True) for family in ("file", "principal", "knowledge", "document", "pin_file")],
)
@pytest.mark.parametrize("oversize", [False, True])
async def test_actual_orm_resource_owners_preserve_typed_rows(
    monkeypatch, family, interleave, oversize
):
    from datetime import UTC, datetime

    from sqlalchemy import Boolean, Integer, column, table

    from app.domain.models.file import File
    from app.domain.models.knowledge_base import KnowledgeBase
    from app.domain.models.user import User
    from app.infrastructure.models.file import FileModel
    from app.infrastructure.models.knowledge_base import KnowledgeBaseModel, KnowledgeDocumentModel
    from app.infrastructure.models.knowledge_version import (
        KnowledgeBaseVersionORM,
        KnowledgeDocumentRevisionORM,
        KnowledgeVersionDocumentORM,
    )
    from app.infrastructure.models.user import UserORM
    from app.infrastructure.repositories.db_file_repository import DBFileRepository
    from app.infrastructure.repositories.db_knowledge_base_repository import (
        DBKnowledgeBaseRepository,
    )
    from app.infrastructure.repositories.db_user_repository import DBUserRepository

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(
        budget=EvidenceBudget(bytes_limit=1024 * 1024, rows_limit=10000, row_limit=1024)
    )
    kb, document, version, revision = (str(UUID(int=n)) for n in range(1, 5))
    now = datetime.now(UTC)
    models = [
        FileModel,
        UserORM,
        KnowledgeBaseModel,
        KnowledgeDocumentModel,
        KnowledgeBaseVersionORM,
        KnowledgeVersionDocumentORM,
        KnowledgeDocumentRevisionORM,
    ]
    file = FileModel.from_domain(
        File(
            id="file",
            filename="fixture",
            key="retained-object",
            owner_user_id="fixture-user",
            content_digest="a" * 64,
            object_identity=str(UUID(int=10)),
        )
    )
    user = UserORM.from_domain(
        User(id="fixture-user", email="fixture@example.invalid", username="fixture")
    )
    knowledge = KnowledgeBaseModel.from_domain(
        KnowledgeBase(id=kb, name="fixture", owner_user_id="fixture-user")
    )
    doc = KnowledgeDocumentModel(
        id=document,
        kb_id=kb,
        title="fixture document",
        source_type="upload",
        source_ref="fixture",
        mime="text/plain",
        page_count=1,
        status="ready",
        created_at=now,
        updated_at=now,
    )
    with engine.begin() as connection:
        for model in models:
            columns = ",".join(
                '"'
                + column.name
                + '" '
                + ("INTEGER" if isinstance(column.type, (Boolean, Integer)) else "TEXT")
                for column in model.__table__.columns
            )
            connection.exec_driver_sql("CREATE TABLE " + model.__tablename__ + " (" + columns + ")")
        for model in (file, user, knowledge, doc):
            connection.execute(
                type(model).__table__.insert(),
                {
                    column.name: getattr(model, column.name)
                    for column in type(model).__table__.columns
                },
            )
        connection.execute(
            KnowledgeBaseVersionORM.__table__.insert(),
            {"id": version, "knowledge_base_id": kb, "state": "ready", "published_at": now},
        )
        connection.execute(
            KnowledgeDocumentRevisionORM.__table__.insert(),
            {"id": revision, "document_id": document, "state": "indexed"},
        )
        connection.execute(
            KnowledgeVersionDocumentORM.__table__.insert(),
            {
                "version_id": version,
                "knowledge_base_id": kb,
                "document_id": document,
                "document_revision_id": revision,
                "state": "indexed",
            },
        )
    original = Connection.execute
    calls = []
    missing_mode = False

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            calls.append("preflight")
            return original(
                connection,
                select(
                    *[
                        literal(value).label(name)
                        for name, value in {
                            "row_count": 0 if missing_mode else 1,
                            "max_bytes": 0 if missing_mode else 5000 if oversize else 40,
                            "total_bytes": 0 if missing_mode else 5000 if oversize else 40,
                            "read_only": "on",
                            "isolation": "repeatable read",
                            "snapshot": "fixture:resource",
                        }.items()
                    ]
                ),
            )
        calls.append("original")
        if "execution_run_projection" in str(statement):
            return IteratorResult(SimpleResultMetaData(["id"]), iter([("intervening",)]))
        if "execution_public_content" in str(statement):
            if missing_mode:
                return IteratorResult(
                    SimpleResultMetaData(["content_id", "citation_refs"]), iter(())
                )
            citation = {
                "availability": "available",
                "resource_kind": "file",
                "file_id": "file",
                "content_digest": "a" * 64,
                "object_identity": str(UUID(int=10)),
            }
            return IteratorResult(
                SimpleResultMetaData(["content_id", "citation_refs"]),
                iter([(UUID(int=20), [citation])]),
            )
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    async with AsyncSession(sync_session_class=Bound) as session:
        from app.infrastructure.repositories import (
            db_file_repository,
            db_knowledge_base_repository,
            db_resource_pin_repository,
            db_user_repository,
        )
        from app.infrastructure.repositories.kb import retrieval_mixin

        module = {
            "file": db_file_repository,
            "principal": db_user_repository,
            "knowledge": db_knowledge_base_repository,
            "document": retrieval_mixin,
            "pin_file": db_resource_pin_repository,
        }.get(family)
        captured = []
        if module is not None:
            original_retain = module.retain_read

            def retain_with_identity(*args, **kwargs):
                source = kwargs.get("source_result")
                captured.append(source)
                assert source is session.sync_session._evidence_latest_result[0]
                if interleave:
                    extra = session.sync_session.execute(
                        select(table("execution_run_projection", column("id")))
                    )
                    extra.close()
                return original_retain(*args, **kwargs)

            monkeypatch.setattr(module, "retain_read", retain_with_identity)

        async def read():
            scope = OwnerScope.personal("fixture-user")
            if family == "file":
                return await DBFileRepository(session).get_by_id("file", scope=scope)
            if family == "principal":
                return await DBUserRepository(session).get_by_id("fixture-user")
            if family == "knowledge":
                return await DBKnowledgeBaseRepository(session).get_kb(kb, scope=scope)
            if family == "pin_file":
                from app.domain.models.resource_pin import ResourceIdentity
                from app.infrastructure.repositories.db_resource_pin_repository import (
                    DBResourcePinRepository,
                )

                return await DBResourcePinRepository(session).resolve(
                    scope,
                    ResourceIdentity(
                        resource_kind="execution_content",
                        resource_id=str(UUID(int=20)),
                        resource_version="a" * 64,
                    ),
                )
            return await DBKnowledgeBaseRepository(session).get_document_for_version(
                kb, version, document
            )

        if oversize:
            with pytest.raises(EvidenceQuotaError):
                await read()
            assert calls == ["preflight"]
            assert not captured
        elif interleave:
            with pytest.raises(ValueError, match="result missing or superseded"):
                await read()
            assert len(captured) == 1
            assert captured[0].closed
            assert not owner.originals.get(
                "principal-source" if family == "principal" else "resource-source"
            )
        else:
            result = await read()
            assert result
            assert calls == ["preflight", "original"] * (2 if family == "pin_file" else 1)
            retained = owner.originals[
                "principal-source" if family == "principal" else "resource-source"
            ]
            assert retained
            assert retained[0]["value"]
            if module is not None:
                assert len(captured) == 1
                assert captured[0].closed
                assert session.sync_session._evidence_latest_result is None
                missing_mode = True
                if family == "pin_file":
                    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
                    from app.infrastructure.repositories.db_resource_pin_repository import (
                        DBResourcePinRepository,
                    )

                    with pytest.raises(ResourceUnavailable):
                        await DBResourcePinRepository(session).resolve(
                            OwnerScope.personal("fixture-user"),
                            ResourceIdentity(
                                resource_kind="execution_content",
                                resource_id=str(UUID(int=99)),
                                resource_version="a" * 64,
                            ),
                        )
                    missing = None
                elif family == "file":
                    missing = await DBFileRepository(session).get_by_id(
                        "missing", scope=OwnerScope.personal("fixture-user")
                    )
                elif family == "principal":
                    missing = await DBUserRepository(session).get_by_id("missing")
                elif family == "document":
                    missing = await DBKnowledgeBaseRepository(session).get_document_for_version(
                        kb, version, str(UUID(int=99))
                    )
                else:
                    missing = await DBKnowledgeBaseRepository(session).get_kb(
                        str(UUID(int=99)), scope=OwnerScope.personal("fixture-user")
                    )
                assert missing is None
                assert (
                    owner.originals[
                        "principal-source" if family == "principal" else "resource-source"
                    ][-1]["value"]
                    is None
                )
                assert len(captured) == 2
                assert captured[-1].closed
                assert session.sync_session._evidence_latest_result is None
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["file", "principal", "knowledge", "document"])
async def test_orm_original_result_closes_on_cardinality_error(family):
    from sqlalchemy.exc import MultipleResultsFound

    from app.infrastructure.repositories.db_file_repository import DBFileRepository
    from app.infrastructure.repositories.db_knowledge_base_repository import (
        DBKnowledgeBaseRepository,
    )
    from app.infrastructure.repositories.db_user_repository import DBUserRepository

    class DuplicateResult:
        closed = False

        def scalar_one_or_none(self):
            raise MultipleResultsFound("duplicate original")

        def one_or_none(self):
            raise MultipleResultsFound("duplicate original")

        def close(self):
            self.closed = True

    class Session:
        def __init__(self):
            self.info = {}

        async def execute(self, *_):
            return duplicate

    duplicate = DuplicateResult()
    session = Session()
    scope = OwnerScope.personal("fixture-user")

    async def read():
        if family == "file":
            return await DBFileRepository(session).get_by_id("file", scope=scope)
        if family == "principal":
            return await DBUserRepository(session).get_by_id("fixture-user")
        if family == "document":
            return await DBKnowledgeBaseRepository(session).get_document_for_version(
                str(UUID(int=1)), str(UUID(int=2)), str(UUID(int=3))
            )
        return await DBKnowledgeBaseRepository(session).get_kb(str(UUID(int=1)), scope=scope)

    with pytest.raises(MultipleResultsFound, match="duplicate original"):
        await read()
    assert duplicate.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "interleave", "cancel"])
async def test_pin_validate_binds_each_original_result_before_nested_resolve(monkeypatch, mode):
    import asyncio

    from sqlalchemy import column, table

    from app.domain.models.resource_pin import ResourceIdentity
    from app.infrastructure.repositories import db_resource_pin_repository as module

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(budget=EvidenceBudget())
    original = Connection.execute
    returned = []
    original_count = 0

    def result(columns, rows):
        item = IteratorResult(SimpleResultMetaData(columns), iter(rows))
        returned.append(item)
        return item

    def driver(connection, statement, parameters=None, *args, **kwargs):
        nonlocal original_count
        if statement.get_execution_options().get("c2c_preflight"):
            count = 0 if original_count == 0 else 1
            return original(
                connection,
                select(
                    literal(count).label("row_count"),
                    literal(0 if count == 0 else 40).label("max_bytes"),
                    literal(0 if count == 0 else 40).label("total_bytes"),
                    literal("on").label("read_only"),
                    literal("repeatable read").label("isolation"),
                    literal("fixture:pin").label("snapshot"),
                ),
            )
        original_count += 1
        sql = str(statement)
        if "FROM resource_pins WHERE" in sql:
            if parameters["resource"] == "missing":
                return result(["available", "unavailable_reason"], [])
            return result(["available", "unavailable_reason"], [(True, None)])
        if "SELECT key,content_digest FROM files" in sql:
            return result(["key", "content_digest"], [("private-key", "a" * 64)])
        return result(["id"], [("intervening",)])

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    missing = ResourceIdentity(
        resource_kind="file", resource_id="missing", resource_version="a" * 64
    )
    available = ResourceIdentity(
        resource_kind="file", resource_id="available", resource_version="a" * 64
    )
    original_retain = module.retain_read
    captured = []
    async with AsyncSession(sync_session_class=Bound) as session:

        def retain_with_identity(*args, **kwargs):
            if args[2] == "pins.validate":
                source = kwargs["source_result"]
                assert source is session.sync_session._evidence_latest_result[0]
                captured.append(source)
                if args[3]["resource"].resource_id == "available":
                    if mode == "interleave":
                        extra = session.sync_session.execute(
                            select(table("execution_run_projection", column("id")))
                        )
                        extra.close()
                    elif mode == "cancel":
                        raise asyncio.CancelledError()
            return original_retain(*args, **kwargs)

        monkeypatch.setattr(module, "retain_read", retain_with_identity)
        repository = module.DBResourcePinRepository(session)
        if mode == "normal":
            validations = await repository.validate(
                OwnerScope.personal("fixture-user"), "run", "owner", [missing, available]
            )
            assert [(v.available, v.reason) for v in validations] == [
                (False, "missing_pin"),
                (True, None),
            ]
            assert [row["operation"] for row in owner.originals["resource-source"]] == [
                "pins.validate",
                "pins.validate",
                "pins.resolve",
            ]
            assert [row["read"]["sql_index"] for row in owner.originals["resource-source"]] == [
                0,
                1,
                2,
            ]
        else:
            expected = ValueError if mode == "interleave" else asyncio.CancelledError
            with pytest.raises(expected):
                await repository.validate(
                    OwnerScope.personal("fixture-user"), "run", "owner", [missing, available]
                )
            assert [row["operation"] for row in owner.originals["resource-source"]] == [
                "pins.validate"
            ]
        assert len(captured) == 2
        assert all(item.closed for item in captured)
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "mode"),
    [
        ("knowledge_base", "normal"),
        ("knowledge_base", "first_row"),
        ("knowledge_base", "missing_owner"),
        ("knowledge_base", "interleave_knowledge_owner"),
        ("artifact", "normal"),
        ("artifact", "missing_artifact_session"),
        ("artifact", "missing_session_owner"),
        ("artifact", "interleave_artifact_session"),
        ("artifact", "interleave_session_owner"),
    ],
)
async def test_pin_owner_scalar_results_bind_before_next_dispatch(monkeypatch, kind, mode):
    from sqlalchemy import column, table

    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from app.infrastructure.repositories import db_resource_pin_repository as module

    engine = create_engine("sqlite://")
    owner = EvidenceOwner(budget=EvidenceBudget())
    original = Connection.execute
    original_count = 0
    returned = []

    def result(columns, rows):
        item = IteratorResult(SimpleResultMetaData(columns), iter(rows))
        returned.append(item)
        return item

    def driver(connection, statement, parameters=None, *args, **kwargs):
        nonlocal original_count
        if statement.get_execution_options().get("c2c_preflight"):
            absent = (
                mode in {"missing_owner", "missing_artifact_session"} and original_count == 0
            ) or (mode == "missing_session_owner" and original_count == 1)
            return original(
                connection,
                select(
                    literal(0 if absent else 1).label("row_count"),
                    literal(0 if absent else 40).label("max_bytes"),
                    literal(0 if absent else 40).label("total_bytes"),
                    literal("on").label("read_only"),
                    literal("repeatable read").label("isolation"),
                    literal("fixture:pin-owner").label("snapshot"),
                ),
            )
        original_count += 1
        sql = str(statement)
        if "FROM knowledge_bases" in sql:
            rows = [] if mode == "missing_owner" else [("kb",)]
            if mode == "first_row":
                rows.append(("later",))
            return result(["id"], rows)
        if "FROM knowledge_base_versions" in sql:
            return result(["id"], [("version",)])
        if "SELECT session_id FROM artifacts" in sql:
            return result(
                ["session_id"], [] if mode == "missing_artifact_session" else [("session",)]
            )
        if "FROM sessions" in sql:
            return result(["id"], [] if mode == "missing_session_owner" else [("session",)])
        if "SELECT version_refs" in sql:
            return result(["storage_key"], [("private-key",)])
        return result(["id"], [("intervening",)])

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    resource = ResourceIdentity(
        resource_kind=kind,
        resource_id="kb" if kind == "knowledge_base" else "artifact",
        resource_version="version" if kind == "knowledge_base" else "1",
    )
    original_retain = module.retain_read
    captured = []
    async with AsyncSession(sync_session_class=Bound) as session:

        def retain_with_identity(*args, **kwargs):
            operation = args[2]
            source = kwargs["source_result"]
            assert source is session.sync_session._evidence_latest_result[0]
            captured.append((operation, source))
            if mode == "interleave_" + operation.removeprefix("pins."):
                extra = session.sync_session.execute(
                    select(table("execution_run_projection", column("id")))
                )
                extra.close()
            return original_retain(*args, **kwargs)

        monkeypatch.setattr(module, "retain_read", retain_with_identity)
        repository = module.DBResourcePinRepository(session)
        if mode in {"normal", "first_row"}:
            assert await repository.resolve(OwnerScope.personal("fixture-user"), resource)
        else:
            expected = ValueError if mode.startswith("interleave") else ResourceUnavailable
            with pytest.raises(expected):
                await repository.resolve(OwnerScope.personal("fixture-user"), resource)
        assert captured
        assert all(source.closed for _, source in captured)
        own_ops = [row["operation"] for row in owner.originals.get("resource-source", [])]
        if mode in {"normal", "first_row"}:
            assert own_ops == (
                ["pins.knowledge_owner", "pins.resolve"]
                if kind == "knowledge_base"
                else ["pins.artifact_session", "pins.session_owner", "pins.resolve"]
            )
        elif mode == "missing_owner":
            assert own_ops == ["pins.knowledge_owner"]
        elif mode == "missing_artifact_session" or mode == "interleave_session_owner":
            assert own_ops == ["pins.artifact_session"]
        elif mode == "missing_session_owner":
            assert own_ops == ["pins.artifact_session", "pins.session_owner"]
        else:
            assert own_ops == []
        if mode.startswith("missing"):
            assert owner.originals["resource-source"][-1]["value"] is None
        assert session.sync_session._evidence_latest_result is None or mode.startswith("interleave")
    engine.dispose()


@pytest.mark.asyncio
async def test_ordinary_repository_session_keeps_behavior_without_sink():
    from app.infrastructure.repositories.db_evaluation_configuration_repository import (
        DBEvaluationConfigurationRepository,
    )

    class Session:
        def __init__(self):
            self.info = {}

        async def execute(self, statement, parameters):
            return IteratorResult(
                SimpleResultMetaData(["body"]), iter([({"ordinary": "unchanged"},)])
            )

    value = await DBEvaluationConfigurationRepository(Session()).get_version(
        OwnerScope.personal("fixture"), "suite", UUID(int=1)
    )
    assert value == {"ordinary": "unchanged"}


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["event", "signed_configuration"])
@pytest.mark.parametrize("oversize", [False, True])
async def test_actual_event_and_signed_configuration_dispatch(monkeypatch, family, oversize):
    from contextlib import asynccontextmanager
    from datetime import UTC

    from scripts.execution_capacity.persistence import PersistedFacts
    from scripts.execution_capacity.test_inventory_readback import completed_run
    from sqlalchemy import Integer
    from sqlalchemy.types import DateTime, TypeDecorator

    from app.domain.models.execution_usage import content_revision
    from app.infrastructure.execution.models import ExecutionEventORM
    from app.infrastructure.execution.postgres_event_store import PostgresEventStore
    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )

    plan, _, events = completed_run()
    scope = OwnerScope.personal(plan.owner_user_id)
    owner = EvidenceOwner()
    engine = create_engine("sqlite://")

    class UTCDateTime(TypeDecorator):
        impl = DateTime
        cache_ok = True

        def process_result_value(self, value, dialect):
            return value.replace(tzinfo=UTC) if value is not None else None

    monkeypatch.setattr(ExecutionEventORM.__table__.c.occurred_at, "type", UTCDateTime())
    with engine.begin() as connection:
        table = ExecutionEventORM.__table__
        columns = ",".join(
            '"' + c.name + '" ' + ("INTEGER" if isinstance(c.type, Integer) else "TEXT")
            for c in table.columns
        )
        connection.exec_driver_sql("CREATE TABLE " + table.name + " (" + columns + ")")
        for event in events:
            values = event.model_dump(mode="python")
            connection.execute(table.insert(), {c.name: values.get(c.name) for c in table.columns})
    secret = "fixture-only-nonserialized"
    body = {
        "stage": "admission",
        "physical_requester": DBPhysicalRequesterRepository(None, signing_secret=secret)._seal(
            {
                "version": 1,
                "scope": "user:" + scope.user_id,
                "run_id": str(plan.run_id),
                "kind": "user",
                "principal": {"user_id": scope.user_id},
            }
        ),
    }
    config_id = content_revision(
        {"run_id": str(plan.run_id), "purpose": "production", "body": body}
    )
    original = Connection.execute
    calls = []

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement.get_execution_options().get("c2c_preflight"):
            calls.append(("preflight", connection, parameters))
            values = {
                "row_count": 3,
                "max_bytes": 2**21 if oversize else 100,
                "total_bytes": 3 * 2**21 if oversize else 300,
                "read_only": "on",
                "isolation": "repeatable read",
                "snapshot": "event-config:1",
            }
            return original(connection, select(*[literal(v).label(k) for k, v in values.items()]))
        calls.append(("typed", connection, parameters))
        if "execution_configurations" in str(statement):
            return IteratorResult(
                SimpleResultMetaData(["id", "body", "purpose"]),
                iter([(config_id, body, "production")]),
            )
        return original(connection, statement, parameters, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    async with AsyncSession(sync_session_class=Bound) as session:

        async def read():
            if family == "event":
                return await PostgresEventStore(session, evidence=owner).load_stream(
                    "run", str(plan.run_id)
                )
            facts = object.__new__(PersistedFacts)
            facts.evidence, facts.scope, facts.scope_key = owner, scope, "user:" + scope.user_id

            @asynccontextmanager
            async def current():
                yield session

            facts.session = current
            return await facts.configuration(plan.run_id, secret, record=False)

        if oversize:
            with pytest.raises(EvidenceQuotaError):
                await read()
            assert [c[0] for c in calls] == ["preflight"]
        else:
            result = await read()
            assert [c[0] for c in calls] == ["preflight", "typed"]
            assert calls[0][1:] == calls[1][1:]
            if family == "event":
                assert [e.event_hash for e in result] == [e.event_hash for e in events]
                assert (
                    owner.originals["event-source"][0][0]["public_payload"]
                    == events[0].public_payload
                )
            else:
                assert result == config_id
                assert owner.originals["signed-configuration"][0][0]["body"] == body
    engine.dispose()
