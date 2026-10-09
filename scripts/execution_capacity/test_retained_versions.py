"""Real read-only service/UOW capture with private SQLite driver injection."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.evidence_owner import EvidenceOwner
from scripts.execution_capacity.observer_session import ObserverSession
from sqlalchemy import create_engine, literal, select
from sqlalchemy.engine import Connection, IteratorResult
from sqlalchemy.engine.result import SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.evaluation.batch_service import BatchService
from app.composition.uow import DBUnitOfWorkDependencies, create_uow_factory
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.security.db_authorization import _AUTHORIZATION_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", [None, "principal", "uow", "params", "missing", "extra", "cursor"]
)
async def test_retained_version_work_uses_real_batch_service_and_exact_uows(monkeypatch, mutation):
    engine = create_engine("sqlite://")
    owner = EvidenceOwner()
    original = Connection.execute
    batch = UUID(int=10)
    scope, principal = OwnerScope.personal("fixture"), Principal(user_id="fixture", token_version=3)
    secret = b"fixture-only-original-cursor-material"
    rows = [
        {
            "id": UUID(int=20 + i),
            "ordinal": i,
            "case_revision_id": UUID(int=30 + i),
            "config_version_id": UUID(int=40),
            "repetition": 0,
            "run_id": UUID(int=50 + i),
            "execution_status": "succeeded",
            "scoring_status": "complete",
            "attempt": 0,
            "revision": 1,
        }
        for i in range(2)
    ]

    def result(columns, values):
        return IteratorResult(SimpleResultMetaData(columns), iter(values))

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement is _AUTHORIZATION_SQL:
            return result(["value"], [("fixture",)])
        if statement.get_execution_options().get("c2c_preflight"):
            count = (
                len([r for r in rows if r["ordinal"] > parameters["after"]][: parameters["limit"]])
                if "evaluation_batch_results r" in str(statement)
                else 1
            )
            return original(
                connection,
                select(
                    *[
                        literal(value).label(key)
                        for key, value in {
                            "row_count": count,
                            "max_bytes": 100,
                            "total_bytes": 100 * count,
                            "read_only": "on",
                            "isolation": "repeatable read",
                            "snapshot": "fixture:1",
                        }.items()
                    ]
                ),
            )
        sql = str(statement)
        if sql.startswith("SELECT status,token_version"):
            return result(["status", "token_version", "global_role"], [("active", 3, "user")])
        if sql.startswith("SELECT * FROM evaluation_batches"):
            value = {
                "id": batch,
                "revision": 1,
                "status": "completed",
                "review_status": "not_required",
                "cleanup_status": "clean",
            }
            return result(list(value), [tuple(value.values())])
        if sql.startswith("SELECT execution_status,count(*)"):
            return result(["execution_status", "count"], [("succeeded", 2)])
        if sql.startswith("SELECT r.*,a.run_id"):
            selected = [r for r in rows if r["ordinal"] > parameters["after"]][
                : parameters["limit"]
            ]
            return result(list(rows[0]), [tuple(row.values()) for row in selected])
        raise AssertionError("unexpected fixture query")

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    factory = create_uow_factory(
        session_factory=lambda: AsyncSession(sync_session_class=Bound),
        dependencies=DBUnitOfWorkDependencies(
            secret_cipher=object(),
            audit_signing_key="fixture",
            audit_signing_key_id="fixture",
            database_authorization_signing_secret="fixture",
        ),
    )
    live = BatchService(
        SimpleNamespace(uow_factory=factory, cursor_secret=secret), preflight_factory=None
    )
    expected = await live.get(scope, principal, batch)
    page = await live.results(scope, principal, batch, limit=1)
    last = await live.results(scope, principal, batch, limit=1, cursor=page["next_cursor"])
    empty = await live.results(scope, principal, batch, limit=1, cursor=page["next_cursor"])
    operands, sql = deepcopy(owner.originals), deepcopy(owner.sql_reads)
    if mutation == "principal":
        operands["principal-source"][0]["value"]["token_version"] = 4
    if mutation == "uow":
        sql[1]["uow"] = 999
    if mutation == "params":
        sql[1]["parameters"]["id"] = UUID(int=999)
    if mutation == "missing":
        operands["batch-source"].pop(0)
    if mutation == "extra":
        operands["batch-source"].append(deepcopy(operands["batch-source"][-1]))
    from scripts.execution_capacity.retained_versions import RetainedVersionWork

    replay = RetainedVersionWork(
        operands, sql, objects=[], cursor_secret=secret, budget=EvidenceBudget()
    )
    actual = BatchService(
        SimpleNamespace(uow_factory=replay, cursor_secret=secret), preflight_factory=None
    )

    async def check():
        assert await actual.get(scope, principal, batch) == expected
        assert await actual.results(scope, principal, batch, limit=1) == page
        assert (
            await actual.results(
                scope,
                principal,
                batch,
                limit=1,
                cursor=page["next_cursor"] + ("x" if mutation == "cursor" else ""),
            )
            == last
        )
        assert (
            await actual.results(scope, principal, batch, limit=1, cursor=page["next_cursor"])
            == empty
        )
        replay.finish()

    if mutation:
        with pytest.raises((ValueError, PermissionError, KeyError)):
            await check()
    else:
        wrong_material = BatchService(
            SimpleNamespace(uow_factory=replay, cursor_secret=b"fixture-wrong-cursor-material"),
            preflight_factory=None,
        )
        with pytest.raises(ValueError, match="invalid_cursor"):
            await wrong_material.results(
                scope, principal, batch, limit=1, cursor=page["next_cursor"]
            )
        await check()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [None, "body", "member", "pin", "registry", "configuration"])
async def test_original_dataset_environment_configuration_services_replay(monkeypatch, mutation):
    import hashlib
    import json

    from scripts.execution_capacity.evidence_objects import EvidenceObjects
    from scripts.execution_capacity.retained_versions import RetainedVersionWork

    from app.application.evaluation.dataset_service import DatasetService
    from app.application.evaluation.environment_service import EnvironmentService
    from app.application.evaluation.suite_service import SuiteService
    from app.domain.evaluation.configuration import ConfigVersion, digest
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.environment import EnvironmentVersion
    from app.domain.models.resource_pin import ResourceIdentity

    engine = create_engine("sqlite://")
    owner = EvidenceOwner()
    scope, principal = OwnerScope.personal("fixture"), Principal(user_id="fixture", token_version=3)
    identity = UUID(int=100)
    resource = ResourceIdentity(
        resource_kind="file", resource_id="fixture-file", resource_version="d" * 64
    )
    case = CaseRevision(
        id=UUID(int=101), case_key="fixture", input="original private input", resources=(resource,)
    )
    body = json.dumps([case.model_dump(mode="json")]).encode()
    member = {
        "id": case.id,
        "revision": 1,
        "case_key": case.case_key,
        "object_id": UUID(int=102),
        "object_index": 0,
        "storage_key": "private/case.json",
        "digest": hashlib.sha256(body).hexdigest(),
        "cleaned_at": None,
    }
    config = ConfigVersion(
        id=UUID(int=103),
        entity_id=UUID(int=104),
        revision=1,
        name="fixture",
        selection={"model_id": "fixture"},
        fingerprint="fixture",
        snapshot={},
    )
    environment = EnvironmentVersion(
        id=UUID(int=105),
        image_digest={"kind": "local_content_id", "value": "sha256:" + "a" * 64},
        fixture_revision="fixture",
        reset_adapter="fixture",
        adapter_revision="fixture",
        healthcheck_revision="fixture",
    )
    original = Connection.execute

    def result(row):
        return IteratorResult(SimpleResultMetaData(list(row)), iter([tuple(row.values())]))

    def driver(connection, statement, parameters=None, *args, **kwargs):
        if statement is _AUTHORIZATION_SQL:
            return result({"value": "fixture"})
        if statement.get_execution_options().get("c2c_preflight"):
            return original(
                connection,
                select(
                    *[
                        literal(value).label(key)
                        for key, value in {
                            "row_count": 1,
                            "max_bytes": 1000,
                            "total_bytes": 1000,
                            "read_only": "on",
                            "isolation": "repeatable read",
                            "snapshot": "fixture:" + str(owner.sql_reads[-1]["uow"]),
                        }.items()
                    ]
                ),
            )
        sql = str(statement)
        if sql.startswith("SELECT status,token_version"):
            return result({"status": "active", "token_version": 3, "global_role": "user"})
        if sql.startswith("SELECT id,dataset_id,revision"):
            return result({"id": identity, "dataset_id": identity, "revision": 1})
        if sql.startswith("SELECT c.*"):
            return result(member)
        if sql.startswith("SELECT available,unavailable_reason"):
            return result({"available": True, "unavailable_reason": None})
        if sql.startswith("SELECT key,content_digest"):
            return result({"key": "private/file", "content_digest": "d" * 64})
        if sql.startswith("SELECT body FROM"):
            return result({"body": config.model_dump(mode="json")})
        if sql.startswith("SELECT revision,body,digest"):
            raw = environment.model_dump(mode="json")
            return result({"revision": 1, "body": raw, "digest": digest(raw)})
        raise AssertionError("unexpected fixture query")

    monkeypatch.setattr(Connection, "execute", driver)

    class Bound(ObserverSession):
        def __init__(self, **kwargs):
            kwargs["bind"] = engine
            super().__init__(budget=owner.budget, evidence=owner, **kwargs)

    factory = create_uow_factory(
        session_factory=lambda: AsyncSession(sync_session_class=Bound),
        dependencies=DBUnitOfWorkDependencies(
            secret_cipher=object(),
            audit_signing_key="fixture",
            audit_signing_key_id="fixture",
            database_authorization_signing_secret="fixture",
        ),
    )

    class OriginalObjects:
        async def get_bounded_bytes(self, key, limit):
            assert key == member["storage_key"]
            return SimpleNamespace(data=body, truncated=False)

    objects = EvidenceObjects(OriginalObjects(), owner.budget, object_limit=8192)
    secret = b"fixture-only-original-secret"

    async def reads(uow, storage):
        datasets = DatasetService(uow, storage, None)
        suites = SuiteService(uow, datasets, limits=None, policies=None, cursor_secret=secret)
        environments = EnvironmentService(uow, None)
        return (
            await datasets.get_version(scope, principal, identity),
            await suites.get_version(scope, principal, "config", config.id),
            await environments.version(scope, principal, environment.id),
        )

    expected = await reads(factory, objects)
    operands, sql, stored = (
        deepcopy(owner.originals),
        deepcopy(owner.sql_reads),
        deepcopy(objects.originals),
    )
    if mutation == "body":
        raw = json.loads(stored[0]["data"])
        raw[0]["id"] = str(UUID(int=999))
        stored[0]["data"] = json.dumps(raw).encode()
        operands["version-source"][1]["value"]["digest"] = hashlib.sha256(
            stored[0]["data"]
        ).hexdigest()
    if mutation == "member":
        operands["version-source"][1]["value"]["case_key"] = "foreign"
    if mutation == "pin":
        operands["resource-source"][0]["value"]["unavailable_reason"] = "revoked"
    if mutation == "registry":
        operands["version-source"][-1]["value"]["body"]["fixture_revision"] = "changed"
    if mutation == "configuration":
        operands["version-source"][-2]["value"]["selection"]["purpose"] = "foreign"
    replay = RetainedVersionWork(
        operands, sql, objects=stored, cursor_secret=secret, budget=EvidenceBudget()
    )

    async def check():
        assert await reads(replay, replay) == expected
        replay.finish()

    if mutation:
        from pydantic import ValidationError

        from app.domain.evaluation.errors import DatasetNotFound, DatasetUnavailable
        from app.domain.models.resource_pin import ResourceUnavailable

        error = {
            "body": DatasetUnavailable,
            "member": DatasetUnavailable,
            "pin": ResourceUnavailable,
            "registry": DatasetNotFound,
            "configuration": ValidationError,
        }[mutation]
        with pytest.raises(error):
            await check()
    else:
        await check()
    engine.dispose()
