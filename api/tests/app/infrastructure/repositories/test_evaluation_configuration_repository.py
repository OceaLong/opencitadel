# ruff: noqa: F811
"""Owned database with the actual ordinary API role, never the shared fixture."""

from uuid import uuid4

import pytest

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture
async def configurations(datasets):
    from sqlalchemy import text

    from app.application.evaluation.suite_service import SuiteService
    from app.domain.evaluation.configuration import DeploymentLimits
    from tests.app.execution_test_support import execution_admin_session

    ds, scope, principal, _, _ = datasets
    from app.domain.runtime_policy import ExecutionPolicy, OperationsPolicy, policy_digest

    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO inference_endpoints(id,display_name,provider,base_url) VALUES ('e02-endpoint','test','openai','https://example.invalid/v1')"
            )
        )
        await db.execute(
            text(
                "INSERT INTO inference_models(id,endpoint_id,display_name,model_name,kind,settings) VALUES ('e02-model','e02-endpoint','model','alias','chat',CAST(:settings AS jsonb))"
            ),
            {"settings": '{"kind":"chat","temperature":0.7,"max_output_tokens":8192}'},
        )
        execution_id, operations_id = uuid4(), uuid4()
        for table_name, identity, policy in [
            ("execution_policy_revisions", execution_id, ExecutionPolicy()),
            ("operations_policy_revisions", operations_id, OperationsPolicy()),
        ]:
            await db.execute(
                text(
                    f"INSERT INTO {table_name}(id,schema_version,payload,digest,created_by,note) VALUES (:id,1,CAST(:payload AS jsonb),:digest,'test','test')"
                ),
                {
                    "id": identity,
                    "payload": policy.model_dump_json(),
                    "digest": policy_digest(1, policy),
                },
            )
        await db.execute(
            text(
                "INSERT INTO runtime_policy_heads(id,version,execution_revision_id,operations_revision_id,updated_by) VALUES ('global',1,:execution,:operations,'test')"
            ),
            {"execution": execution_id, "operations": operations_id},
        )
        await db.commit()
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.postgres_runtime_policy_repository import (
        PostgresRuntimePolicyRepository,
    )

    policies = PostgresRuntimePolicyRepository(
        session_factory=ds.uow_factory().session_factory,
        authorization=AuthorizationContext.system("runtime-policy-reader"),
    )
    service = SuiteService(
        ds.uow_factory,
        ds,
        limits=lambda: DeploymentLimits(),
        policies=policies,
        cursor_secret=b"e02-test-cursor-key-only",
    )
    return service, ds, scope, principal


async def test_configuration_publication_is_immutable_and_scoped(configurations):
    from sqlalchemy import text

    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.models.scope import OwnerScope, Principal
    from tests.app.application.services.test_artifact_provenance_postgres import seed
    from tests.app.execution_test_support import execution_admin_session

    service, _ds, scope, principal = configurations
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="One",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    version = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    assert version.version_unpinned
    assert version.snapshot["identity"]["configured_model"] == "alias"
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE inference_models SET model_name='changed' WHERE id='e02-model'")
        )
        await db.commit()
    loaded = await service.get_version(scope, principal, "config", version.id)
    assert loaded.snapshot["identity"]["configured_model"] == "alias"
    other, _ = await seed()
    with pytest.raises((DatasetNotFound, PermissionError)):
        await service.get_version(
            OwnerScope.personal(other), Principal(user_id=other), "config", version.id
        )


async def published_suite(configurations):
    from app.domain.evaluation.configuration import ConfigSelection, SuiteDefinition, SuiteSettings
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.rubric import RubricDefinition

    service, ds, scope, principal = configurations

    async def publish(kind, definition):
        draft = await service.create(
            scope,
            principal,
            kind=kind,
            name=kind,
            definition=definition.model_dump(mode="json"),
            request_id=str(uuid4()),
        )
        return await service.publish(
            scope,
            principal,
            kind=kind,
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )

    subject = await publish("config", ConfigSelection(model_id="e02-model"))
    judge = await publish(
        "config", ConfigSelection(model_id="e02-model", purpose="evaluation_judge", mode="ask")
    )
    rubric = await publish("rubric", RubricDefinition(judge_config_version=judge.id))
    draft = await ds.create_draft(
        scope, principal, request_id=str(uuid4()), expected_revision=0, name="Dataset"
    )
    draft = await ds.update_case(
        scope,
        principal,
        dataset_id=draft.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(case_key="one", input="Question"),
    )
    dataset = await ds.publish(
        scope, principal, dataset_id=draft.id, expected_revision=2, request_id=str(uuid4())
    )
    suite = await publish(
        "suite",
        SuiteDefinition(
            dataset_version=dataset.id,
            config_versions=(subject.id,),
            rubric_version=rubric.id,
            mode="recorded",
            settings=SuiteSettings(token_budget=100000),
        ),
    )
    return suite, subject


async def test_preflight_persists_revisions_and_revalidates_current_metadata(
    configurations, monkeypatch
):
    from sqlalchemy import text

    from app.application.evaluation.preflight import PreflightService
    from app.application.execution.agent_tool_catalog import AgentToolCatalog
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from tests.app.execution_test_support import execution_admin_session

    service, ds, scope, principal = configurations
    suite, _subject = await published_suite(configurations)

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight runtime side effect")

    monkeypatch.setattr(ds.objects, "get_bytes", forbidden)
    monkeypatch.setattr(ApiKeyCipher, "decrypt_versioned", forbidden)
    monkeypatch.setattr(AgentToolCatalog, "definitions", forbidden)
    from app.application.execution.admission import RunAdmissionService
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.external.sandbox import SandboxFactoryPort
    from app.domain.services.tools.a2a import A2ATool
    from app.domain.services.tools.mcp import MCPTool
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    for cls, name in (
        (MCPTool, "initialize"),
        (A2ATool, "initialize"),
        (RunAdmissionService, "admit"),
        (InferenceModelService, "probe_model"),
        (OpenAILLM, "invoke"),
        (SandboxFactoryPort, "create"),
    ):
        monkeypatch.setattr(cls, name, forbidden)

    preflight = PreflightService(service, principal)
    first = await preflight.check(scope, suite.id)
    assert first.quantity == 1
    assert first.price_coverage == "unknown"
    assert "budget_authority_unavailable" in first.errors
    assert first.revision == 1
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE inference_models SET model_name='drift' WHERE id='e02-model'")
        )
        await db.commit()
    second = await preflight.check(scope, suite.id)
    assert second.revision == 2
    assert "model_changed" in second.errors
    current = await preflight.revalidate_for_start(scope, suite.id)
    assert not current.allowed
    assert current.id != first.id
    async with execution_admin_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM evaluation_preflights")) == 2
        assert await db.scalar(text("SELECT count(*) FROM execution_stream_owners")) == 0


async def test_configuration_audit_failure_rolls_back_publication(configurations, monkeypatch):
    from sqlalchemy import text

    from app.domain.evaluation.configuration import ConfigSelection
    from app.infrastructure.repositories.db_audit_repository import DBAuditRepository
    from tests.app.execution_test_support import execution_admin_session

    service, _ds, scope, principal = configurations
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Atomic",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )

    async def fail(*args, **kwargs):
        raise RuntimeError("audit failure")

    monkeypatch.setattr(DBAuditRepository, "add", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        await service.publish(
            scope,
            principal,
            kind="config",
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )
    async with execution_admin_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM evaluation_config_versions")) == 0
        assert await db.scalar(text("SELECT count(*) FROM evaluation_mutations")) == 1
    assert (await service.get_draft(scope, principal, "config", draft.id)).revision == 1


async def test_successful_metadata_preflight_never_authorizes_later_stricter_policy(configurations):
    from sqlalchemy import text

    from app.application.evaluation.preflight import DependencyEvidence, PreflightService
    from app.domain.runtime_policy import AgentExecutionPolicy
    from tests.app.execution_test_support import execution_admin_session

    service, _, scope, principal = configurations
    suite, _ = await published_suite(configurations)

    # Explicit stand-in only for future E03/E05 read-only authorities; no execution capability claimed.
    class Metadata:
        async def check(self, scope, suite, configs, *, for_start):
            return DependencyEvidence(
                ready=True, revision="test-metadata-v1", physical_call_upper_bound=10
            )

    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE inference_endpoints SET credential='not-a-real-secret-cipher' WHERE id='e02-endpoint'"
            )
        )
        await db.commit()
    gate = PreflightService(service, principal, recordings=Metadata(), budgets=Metadata())
    first = await gate.check(scope, suite.id)
    assert first.allowed
    assert first.quantity == 1
    assert first.physical_call_upper_bound == 10
    active = await service.policies.load_active_pair()
    tighter = active.execution.revision.policy.model_copy(
        update={"agent": AgentExecutionPolicy(max_iterations=1)}
    )
    await service.policies.create_and_activate_execution(
        policy=tighter,
        expected_head_version=active.execution.head.version,
        expected_active_revision_id=active.execution.revision.id,
        actor="test",
        note="tighter",
    )
    current = await gate.revalidate_for_start(scope, suite.id)
    assert not current.allowed
    assert "policy_changed" in current.errors
    assert current.evidence["policy"] != first.evidence["policy"]


async def test_auditor_is_read_only_and_current_principal_revocation_rolls_back(
    configurations, monkeypatch
):
    from sqlalchemy import text

    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from app.infrastructure.repositories.db_evaluation_configuration_repository import (
        DBEvaluationConfigurationRepository,
    )
    from tests.app.execution_test_support import execution_admin_session

    service, _, scope, principal = configurations
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Revocation",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    original = DBEvaluationConfigurationRepository.metadata

    async def revoke(self, *args, **kwargs):
        value = await original(self, *args, **kwargs)
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET global_role='auditor' WHERE id=:id"),
                {"id": principal.user_id},
            )
            await db.commit()
        return value

    monkeypatch.setattr(DBEvaluationConfigurationRepository, "metadata", revoke)
    with pytest.raises(PermissionError):
        await service.publish(
            scope,
            principal,
            kind="config",
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )
    auditor = Principal(user_id=principal.user_id, global_role=GlobalRole.AUDITOR)
    assert (await service.get_draft(scope, auditor, "config", draft.id)).id == draft.id
    with pytest.raises(PermissionError):
        await service.create(
            scope,
            auditor,
            kind="config",
            name="Denied",
            definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
            request_id=str(uuid4()),
        )
    async with execution_admin_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM evaluation_config_versions")) == 0


async def test_required_reference_and_independent_judge_are_publication_gates(configurations):
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.evaluation.rubric import RubricDefinition

    service, _, scope, principal = configurations
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Subject",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    subject = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    rubric = await service.create(
        scope,
        principal,
        kind="rubric",
        name="Invalid judge",
        definition=RubricDefinition(judge_config_version=subject.id).model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    with pytest.raises(ValueError, match="independent_judge_required"):
        await service.publish(
            scope,
            principal,
            kind="rubric",
            entity_id=rubric.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )
    suite, _ = await published_suite(configurations)
    original = await service.get_version(scope, principal, "rubric", suite.rubric_version)
    required = await service.create(
        scope,
        principal,
        kind="rubric",
        name="Required",
        definition=RubricDefinition(
            judge_config_version=original.judge_config_version, reference_policy="required"
        ).model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    required = await service.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=required.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    definition = suite.model_dump(
        mode="json",
        exclude={"id", "entity_id", "revision", "name", "quantity", "fingerprint", "dataset_proof"},
    )
    definition["rubric_version"] = str(required.id)
    candidate = await service.create(
        scope,
        principal,
        kind="suite",
        name="Missing reference",
        definition=definition,
        request_id=str(uuid4()),
    )
    with pytest.raises(ValueError, match="reference_required"):
        await service.publish(
            scope,
            principal,
            kind="suite",
            entity_id=candidate.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )


async def test_duplicate_publication_is_conflict_not_database_error(configurations):
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.evaluation.errors import DatasetConflict

    service, _, scope, principal = configurations
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Duplicate",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    with pytest.raises(DatasetConflict):
        await service.publish(
            scope,
            principal,
            kind="config",
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )


async def test_inline_credential_text_is_rejected_before_publication(configurations):
    from app.domain.evaluation.configuration import ConfigSelection

    service, _, scope, principal = configurations
    with pytest.raises(ValueError, match="sensitive_configuration_text"):
        await service.create(
            scope,
            principal,
            kind="config",
            name="Secret",
            definition=ConfigSelection(
                model_id="e02-model", prompt='{"api_key":"do-not-store-me"}'
            ).model_dump(mode="json"),
            request_id=str(uuid4()),
        )


async def test_config_pins_do_not_authorize_forced_deleted_dependency(configurations, monkeypatch):
    from sqlalchemy import text

    from app.application.evaluation.preflight import PreflightService
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.models.resource_pin import ResourceIdentity
    from tests.app.execution_test_support import execution_admin_session

    service, ds, scope, principal = configurations
    file_id = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO files(id,owner_user_id,key,content_digest,object_identity) VALUES (:id,:owner,'test-fixed-file',:digest,:object)"
            ),
            {"id": file_id, "owner": principal.user_id, "digest": "a" * 64, "object": uuid4()},
        )
        await db.commit()
    resource = ResourceIdentity(
        resource_kind="file", resource_id=file_id, resource_version="a" * 64
    )
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Pinned",
        definition=ConfigSelection(model_id="e02-model", resources=(resource,)).model_dump(
            mode="json"
        ),
        request_id=str(uuid4()),
    )
    version = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    suite, _ = await published_suite(configurations)
    definition = suite.model_dump(
        mode="json",
        exclude={"id", "entity_id", "revision", "name", "quantity", "fingerprint", "dataset_proof"},
    )
    definition["config_versions"] = [str(version.id)]
    draft = await service.create(
        scope,
        principal,
        kind="suite",
        name="Pinned suite",
        definition=definition,
        request_id=str(uuid4()),
    )
    suite = await service.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("object transport used")

    monkeypatch.setattr(ds.objects, "get_bytes", forbidden)
    async with execution_admin_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM resource_pins WHERE owner_kind='config_version'")
            )
            == 1
        )
        await db.execute(
            text("UPDATE files SET content_available=false WHERE id=:id"), {"id": file_id}
        )
        await db.commit()
    check = await PreflightService(service, principal).check(scope, suite.id)
    assert not check.allowed
    assert "configuration_unavailable" in check.errors


async def test_crud_replays_original_result_and_keeps_published_history(configurations):
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound

    service, _, scope, principal = configurations
    definition = ConfigSelection(model_id="e02-model").model_dump(mode="json")
    request = str(uuid4())
    first = await service.create(
        scope, principal, kind="config", name="Original", definition=definition, request_id=request
    )
    version = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=first.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    await service.update(
        scope,
        principal,
        kind="config",
        entity_id=first.id,
        name="Edited",
        definition=definition,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    replay = await service.create(
        scope, principal, kind="config", name="Original", definition=definition, request_id=request
    )
    assert replay.revision == 1
    assert replay.name == "Original"
    with pytest.raises(DatasetConflict):
        await service.update(
            scope,
            principal,
            kind="config",
            entity_id=first.id,
            name="Lost",
            definition=definition,
            expected_revision=1,
            request_id=str(uuid4()),
        )
    delete_request = str(uuid4())
    removed = await service.delete(
        scope,
        principal,
        kind="config",
        entity_id=first.id,
        expected_revision=2,
        request_id=delete_request,
    )
    again = await service.delete(
        scope,
        principal,
        kind="config",
        entity_id=first.id,
        expected_revision=2,
        request_id=delete_request,
    )
    assert again == removed
    with pytest.raises(DatasetNotFound):
        await service.get_draft(scope, principal, "config", first.id)
    assert (await service.get_version(scope, principal, "config", version.id)).name == "Original"


async def test_ordinary_role_cannot_rewrite_versions_or_forge_audit_intent(configurations):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.models.audit_log import AuditLog

    service, _, scope, principal = configurations
    request = str(uuid4())
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Immutable",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=request,
    )
    version = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    async with service.uow_factory(service.auth(scope, principal, request)) as uow:
        assert await uow.db_session.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        )
        assert await uow.db_session.scalar(
            text(
                "SELECT relowner <> (SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_class WHERE oid='evaluation_config_versions'::regclass"
            )
        )
        with pytest.raises(PermissionError, match="receipt mismatch"):
            await uow.audit.add_evaluation(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.rubric.create",
                    resource_type="evaluation_rubric",
                    resource_id=str(draft.id),
                    request_id=request,
                    metadata={"revision": 1},
                ),
                authorization=service.auth(scope, principal, request),
            )
        assert (
            await uow.db_session.scalar(text("SELECT count(*) FROM evaluation_config_versions"))
            == 1
        )
    async with service.uow_factory(service.auth(scope, principal)) as uow:
        with pytest.raises(DBAPIError):
            await uow.db_session.execute(
                text("UPDATE evaluation_config_versions SET name='changed' WHERE id=:id"),
                {"id": version.id},
            )
    assert (await service.get_version(scope, principal, "config", version.id)).name == "Immutable"


async def test_publication_and_preflight_complete_with_one_connection_pool(
    configurations, monkeypatch
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.evaluation.preflight import PreflightService
    from app.domain.evaluation.configuration import ConfigSelection
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.postgres_runtime_policy_repository import (
        PostgresRuntimePolicyRepository,
    )

    service, _, scope, principal = configurations
    suite, _ = await published_suite(configurations)
    original = service.uow_factory
    original_factory = original().session_factory
    engine = create_async_engine(original_factory.kw["bind"].url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(
        engine, expire_on_commit=False, info=original_factory.kw.get("info", {})
    )

    def single(authorization_context=None):
        uow = original(authorization_context)
        uow.session_factory = factory
        return uow

    service.uow_factory = single
    service.policies = PostgresRuntimePolicyRepository(
        session_factory=factory, authorization=AuthorizationContext.system("runtime-policy-reader")
    )
    try:
        draft = await service.create(
            scope,
            principal,
            kind="config",
            name="Pool one",
            definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
            request_id=str(uuid4()),
        )
        published = await asyncio.wait_for(
            service.publish(
                scope,
                principal,
                kind="config",
                entity_id=draft.id,
                expected_revision=1,
                request_id=str(uuid4()),
            ),
            timeout=3,
        )
        assert published.selection.model_id == "e02-model"
        checked = await asyncio.wait_for(
            PreflightService(service, principal).check(scope, suite.id), timeout=3
        )
        assert checked.quantity == 1
        actual_pair = await service.policies.load_active_pair()

        async def forbidden_policy_read():
            raise AssertionError("independent policy checkout inside borrowed UoW")

        monkeypatch.setattr(service.policies, "load_active_pair", forbidden_policy_read)
        gate = PreflightService(service, principal)
        async with single(service.auth(scope, principal)) as uow:
            with pytest.raises(ValueError, match="actual_policy_pair_required"):
                await gate.revalidate_for_start(scope, suite.id, uow=uow)
            borrowed = await gate.revalidate_for_start(
                scope, suite.id, uow=uow, policy_pair=actual_pair
            )
            assert borrowed.evidence["policy"] == str(actual_pair.execution.revision.id)
    finally:
        await engine.dispose()


async def test_legacy_empty_settings_freeze_actual_runtime_defaults(configurations):
    from sqlalchemy import text

    from app.domain.evaluation.configuration import ConfigSelection
    from tests.app.execution_test_support import execution_admin_session

    service, _, scope, principal = configurations
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE inference_models SET settings='{}', capabilities='{}' WHERE id='e02-model'"
            )
        )
        await db.commit()
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Defaults",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    version = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    assert version.snapshot["settings"]["temperature"] == 0.7
    assert version.snapshot["settings"]["max_output_tokens"] == 8192
    assert version.snapshot["capabilities"]["structured_output"] == "auto"
