# ruff: noqa: F401,F811
"""C1 caller-owned transaction tests with real published B authority."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.execution_test_support import execution_admin_session, execution_kernel_database_uri
from tests.app.infrastructure.repositories.test_evaluation_budget_publication import (
    native_inventory,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
    published_suite,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture
async def budget_binding_fixture(configurations, isolated_database, request):
    from app.domain.evaluation.configuration import SuiteDefinition, SuiteSettings
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_uow import DBUnitOfWork
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from core.config import load_deployment_settings

    service, _, scope, principal = configurations
    await native_inventory(configurations)
    options = getattr(request, "param", {})
    if options.get("price") is not None:
        from app.application.evaluation.budget_service import BudgetAuthority
        from app.domain.evaluation.budget_capabilities import BudgetInventory

        inventory = service.budgets.inventory.model_dump(mode="json")
        for profile in inventory["profiles"]:
            profile["price"] = options["price"]
        service.budgets = BudgetAuthority(BudgetInventory.model_validate(inventory))
    old, config = await published_suite(configurations)
    definition = SuiteDefinition(
        dataset_version=old.dataset_version,
        config_versions=old.config_versions,
        rubric_version=old.rubric_version,
        mode="recorded",
        settings=SuiteSettings(
            token_budget=options.get("token_budget", 3000000),
            money_budget=options.get("money_budget"),
        ),
    )
    draft = await service.create(
        scope,
        principal,
        kind="suite",
        name="Budget binding",
        definition=definition.model_dump(mode="json"),
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
    pair = await service.policies.load_active_pair()
    engine, _ = isolated_database
    kernel = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=engine.url.database),
        pool_size=1,
        max_overflow=0,
    )
    settings = load_deployment_settings()

    def uow(authorization=None):
        return DBUnitOfWork(
            async_sessionmaker(kernel, expire_on_commit=False),
            secret_cipher=ApiKeyCipher("test"),
            audit_signing_key="test",
            audit_signing_key_id="test",
            database_authorization_signing_secret=settings.database_authorization_signing_secret,
            authorization_context=authorization
            or AuthorizationContext.for_principal(principal, scope=scope),
        )

    try:
        from app.domain.evaluation.recording import RecordingJob, RecordingManifest

        async with uow() as work:
            job = RecordingJob(id=uuid4(), source_run_id=uuid4())
            await work.evaluation_recording.create(scope, job, [], principal)
            token = await work.evaluation_recording.claim(scope, job.id)
            manifest = RecordingManifest(
                id=uuid4(),
                job_id=job.id,
                source_run_id=job.source_run_id,
                catalog_fingerprint="empty-tools",
                contracts=(),
                slots=(),
                pins=(),
            )
            await work.evaluation_recording.publish(scope, manifest, token)
            await work.commit()
        definition = definition.model_copy(update={"recording_versions": (manifest.id,)})
        draft = await service.create(
            scope,
            principal,
            kind="suite",
            name="Recorded budget binding",
            definition=definition.model_dump(mode="json"),
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
        yield service, scope, principal, suite, config, pair, uow
    finally:
        await kernel.dispose()


async def test_namespace_derives_published_budget_and_replay_binding_is_same_transaction(
    budget_binding_fixture,
):
    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.evaluation.recording import RecordingJob, RecordingManifest
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    service, scope, principal, suite, config, pair, uow = budget_binding_fixture
    namespace_id, run_id = uuid4(), uuid4()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.AGENT)
    async with uow() as work:
        namespace = await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=namespace_id,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        assert namespace.token_budget == 3000000
        assert namespace.requester["user_id"] == principal.user_id
        case_id = namespace.case_ids[0]
        await prepare_replay_binding(
            work,
            service,
            scope,
            principal,
            run_id=run_id,
            source_entity_id="case-one",
            config_version_id=config.id,
            recording_version_id=suite.recording_versions[0],
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        selection = BudgetBindingSelection(
            namespace_id=namespace_id,
            run_id=run_id,
            source_entity_id="case-one",
            case_id=case_id,
            config_version_id=config.id,
            subject_config_version_id=config.id,
            repeat=1,
        )
        bound = await prepare_budget_binding(
            work,
            service,
            scope,
            principal,
            selection=selection,
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        assert bound.purpose == "evaluation_subject"
        assert bound.candidate_proof == config.snapshot["budget"]
        assert bound.source_entity_type == "evaluation_recorded_case"
        assert (
            await prepare_budget_binding(
                work,
                service,
                scope,
                principal,
                selection=selection,
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
            == bound
        )
        await work.commit()
    async with uow() as work:
        assert (
            await work.evaluation_budget_control.binding(scope, run_id)
        ).namespace_id == namespace_id
        assert (await work.evaluation_recording.binding(scope, run_id))["admission"][
            "source_entity_id"
        ] == "case-one"


async def test_namespace_kernel_only_immutable_and_rollback(budget_binding_fixture):
    from sqlalchemy.exc import DBAPIError

    from app.application.evaluation.budget_admission import prepare_budget_namespace
    from app.domain.evaluation.errors import DatasetNotFound

    service, scope, principal, suite, _, pair, uow = budget_binding_fixture
    identity = uuid4()
    async with uow() as work:
        await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=identity,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        # No commit: a failed combined source/budget preparation must leave no control row.
    async with uow() as work:
        with pytest.raises(DatasetNotFound):
            await work.evaluation_budget_control.namespace(scope, identity)
    async with service.uow_factory(service.auth(scope, principal)) as work:
        with pytest.raises(DBAPIError):
            await prepare_budget_namespace(
                work,
                service,
                scope,
                principal,
                namespace_id=identity,
                suite_version_id=suite.id,
                policy_pair=pair,
            )
    async with uow() as work:
        await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=identity,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        await work.commit()
    async with uow() as work:
        with pytest.raises(DBAPIError):
            await work.db_session.execute(
                text("UPDATE evaluation_budget_namespaces SET body='{}' WHERE id=:id"),
                {"id": identity},
            )


async def test_binding_membership_collision_current_requester_and_closed_namespace(
    budget_binding_fixture,
):
    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.evaluation.errors import DatasetConflict
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    service, scope, principal, suite, config, pair, uow = budget_binding_fixture
    identity = uuid4()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.ASK)
    async with uow() as work:
        namespace = await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=identity,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        selection = BudgetBindingSelection(
            namespace_id=identity,
            run_id=uuid4(),
            source_entity_id="judge-one",
            case_id=namespace.case_ids[0],
            config_version_id=namespace.judge_config_version,
            subject_config_version_id=config.id,
            repeat=1,
        )
        for field, value in (
            ("case_id", uuid4()),
            ("subject_config_version_id", uuid4()),
            ("repeat", 2),
        ):
            with pytest.raises(ValueError, match="budget_case_membership_invalid"):
                await prepare_budget_binding(
                    work,
                    service,
                    scope,
                    principal,
                    selection=selection.model_copy(update={field: value}),
                    policy_pair=pair,
                    policy_snapshot=snapshot,
                )
        bound = await prepare_budget_binding(
            work,
            service,
            scope,
            principal,
            selection=selection,
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        assert bound.purpose == "evaluation_judge"
        with pytest.raises(DatasetConflict, match="budget_binding_changed"):
            await prepare_budget_binding(
                work,
                service,
                scope,
                principal,
                selection=selection.model_copy(update={"source_entity_id": "changed"}),
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
        await work.commit()
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    async with uow() as work:
        with pytest.raises(PermissionError, match="revoked"):
            await prepare_budget_binding(
                work,
                service,
                scope,
                principal,
                selection=selection.model_copy(update={"run_id": uuid4()}),
                policy_pair=pair,
                policy_snapshot=snapshot,
            )


async def test_namespace_close_serializes_with_other_worker_binding_and_rejects_late_revision(
    budget_binding_fixture,
):
    import asyncio

    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.evaluation.errors import DatasetConflict
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    service, scope, principal, suite, config, pair, uow = budget_binding_fixture
    identity = uuid4()
    async with uow() as work:
        namespace = await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=identity,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        await work.commit()
    selection = BudgetBindingSelection(
        namespace_id=identity,
        run_id=uuid4(),
        source_entity_id="judge-race",
        case_id=namespace.case_ids[0],
        config_version_id=namespace.judge_config_version,
        subject_config_version_id=config.id,
        repeat=1,
    )
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.ASK)
    second = create_async_engine(uow().session_factory.kw["bind"].url, pool_size=1, max_overflow=0)
    locked, release, attempting = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def closer():
        async with uow() as work:
            result = await work.evaluation_budget_control.close(
                scope, identity, expected_revision=1
            )
            assert result.state == "closed"
            assert result.revision == 2
            locked.set()
            await release.wait()
            await work.commit()

    async def binder():
        await locked.wait()
        work = uow()
        work.session_factory = async_sessionmaker(second, expire_on_commit=False)
        async with work:
            attempting.set()
            await prepare_budget_binding(
                work,
                service,
                scope,
                principal,
                selection=selection,
                policy_pair=pair,
                policy_snapshot=snapshot,
            )

    tasks = [asyncio.create_task(closer()), asyncio.create_task(binder())]
    try:
        await asyncio.wait_for(attempting.wait(), 3)
        await asyncio.sleep(0.05)
        assert not tasks[1].done()
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await second.dispose()
    assert results[0] is None
    assert isinstance(results[1], ValueError)
    assert str(results[1]) == "budget_namespace_closed"
    async with uow() as work:
        assert await work.evaluation_budget_control.binding(scope, selection.run_id) is None
        assert (
            await work.evaluation_budget_control.close(scope, identity, expected_revision=1)
        ).revision == 2
        with pytest.raises(DatasetConflict, match="revision_changed"):
            await work.evaluation_budget_control.close(scope, identity, expected_revision=2)


async def test_failed_source_and_budget_prebinding_roll_back_together(budget_binding_fixture):
    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.application.evaluation.replay_admission import prepare_replay_binding
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot

    service, scope, principal, suite, config, pair, uow = budget_binding_fixture
    identity, run_id = uuid4(), uuid4()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.AGENT)
    with pytest.raises(ValueError, match="budget_source_binding_mismatch"):  # noqa: PT012 - assert rollback of the complete borrowed transaction
        async with uow() as work:
            namespace = await prepare_budget_namespace(
                work,
                service,
                scope,
                principal,
                namespace_id=identity,
                suite_version_id=suite.id,
                policy_pair=pair,
            )
            await prepare_replay_binding(
                work,
                service,
                scope,
                principal,
                run_id=run_id,
                source_entity_id="correct-source",
                config_version_id=config.id,
                recording_version_id=suite.recording_versions[0],
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
            selection = BudgetBindingSelection(
                namespace_id=identity,
                run_id=run_id,
                source_entity_id="wrong-source",
                case_id=namespace.case_ids[0],
                config_version_id=config.id,
                subject_config_version_id=config.id,
                repeat=1,
            )
            await prepare_budget_binding(
                work,
                service,
                scope,
                principal,
                selection=selection,
                policy_pair=pair,
                policy_snapshot=snapshot,
            )
    async with uow() as work:
        assert await work.evaluation_recording.binding(scope, run_id) is None
        assert await work.evaluation_budget_control.binding(scope, run_id) is None
        with pytest.raises(DatasetNotFound):
            await work.evaluation_budget_control.namespace(scope, identity)


async def test_isolated_prebinding_uses_actual_e04_namespace_and_rejects_quarantined_lease(
    budget_binding_fixture,
):
    from datetime import UTC, datetime, timedelta

    from app.application.evaluation.budget_admission import (
        prepare_budget_binding,
        prepare_budget_namespace,
    )
    from app.application.evaluation.environment_admission import prepare_environment_binding
    from app.application.ports.evaluation_environment import AdapterRegistry
    from app.domain.evaluation.budget_binding import BudgetBindingSelection
    from app.domain.evaluation.configuration import SuiteDefinition, SuiteSettings
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease, transition
    from app.domain.execution.family import RunFamily
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
    from tests.app.infrastructure.repositories.test_evaluation_environment_repository import version

    service, scope, principal, recorded_suite, config, pair, uow = budget_binding_fixture
    value = version()

    class Adapter:
        revision = "1"
        fixture_revisions = frozenset({"1"})
        healthcheck_revisions = frozenset({"1"})
        tool_names = frozenset()

        def validate(self, value, targets):
            assert targets == ()

    registry = AdapterRegistry(adapters={"test": Adapter()})
    from app.domain.models.authorization import AuthorizationContext

    async with uow(AuthorizationContext.system("environment-test")) as work:
        await work.evaluation_environment.register(scope, "environment", value)
        await work.commit()
    definition = SuiteDefinition(
        dataset_version=recorded_suite.dataset_version,
        config_versions=recorded_suite.config_versions,
        rubric_version=recorded_suite.rubric_version,
        mode="isolated",
        environment_version=value.id,
        settings=SuiteSettings(token_budget=3000000),
    )
    draft = await service.create(
        scope,
        principal,
        kind="suite",
        name="Isolated budget",
        definition=definition.model_dump(mode="json"),
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
    namespace_id, run_id = uuid4(), uuid4()
    snapshot = derive_run_policy_snapshot(pair.execution, RunFamily.AGENT)
    # E04 allocation is a kernel operation; the immutable original requester
    # still drives every publication/membership check in this caller transaction.
    async with uow(AuthorizationContext.system("execution-kernel")) as work:
        namespace = await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=namespace_id,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        lease = EnvironmentLease(
            id=uuid4(),
            environment_version=value.id,
            case_slot=CaseSlot(
                workspace="user:" + scope.user_id,
                batch_id=namespace_id,
                case_id=namespace.case_ids[0],
                config_version=config.id,
                repeat=1,
            ),
            generation=1,
            revision=1,
            state="allocated",
            requester=principal.model_dump(mode="json"),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await work.evaluation_environment.allocate(scope, lease, (), concurrency=2)
        preparing = transition(lease, "preparing")
        await work.evaluation_environment.save(scope, lease, preparing)
        ready = transition(preparing, "ready")
        await work.evaluation_environment.save(scope, preparing, ready)
        await prepare_environment_binding(
            work,
            service,
            scope,
            principal,
            run_id=run_id,
            source_entity_id="isolated-one",
            config_version_id=config.id,
            lease_id=lease.id,
            policy_pair=pair,
            policy_snapshot=snapshot,
            registry=registry,
        )
        selection = BudgetBindingSelection(
            namespace_id=namespace_id,
            run_id=run_id,
            source_entity_id="isolated-one",
            case_id=namespace.case_ids[0],
            config_version_id=config.id,
            subject_config_version_id=config.id,
            repeat=1,
        )
        bound = await prepare_budget_binding(
            work,
            service,
            scope,
            principal,
            selection=selection,
            policy_pair=pair,
            policy_snapshot=snapshot,
        )
        assert bound.source_entity_type == "evaluation_isolated_case"
        leased = await work.evaluation_environment.lease(scope, lease.id)
        await work.evaluation_environment.save(scope, leased, transition(leased, "quarantine"))
        with pytest.raises(ValueError, match="budget_environment_membership_invalid"):
            await prepare_budget_binding(
                work,
                service,
                scope,
                principal,
                selection=selection,
                policy_pair=pair,
                policy_snapshot=snapshot,
            )


async def test_namespace_identity_conflict_and_scoped_reads(budget_binding_fixture):
    from app.application.evaluation.budget_admission import prepare_budget_namespace
    from app.domain.evaluation.configuration import SuiteDefinition, SuiteSettings
    from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
    from app.domain.models.scope import OwnerScope

    service, scope, principal, suite, _, pair, uow = budget_binding_fixture
    identity = uuid4()
    async with uow() as work:
        first = await prepare_budget_namespace(
            work,
            service,
            scope,
            principal,
            namespace_id=identity,
            suite_version_id=suite.id,
            policy_pair=pair,
        )
        assert (
            await prepare_budget_namespace(
                work,
                service,
                scope,
                principal,
                namespace_id=identity,
                suite_version_id=suite.id,
                policy_pair=pair,
            )
            == first
        )
        await work.commit()
    definition = SuiteDefinition(
        dataset_version=suite.dataset_version,
        config_versions=suite.config_versions,
        rubric_version=suite.rubric_version,
        recording_versions=suite.recording_versions,
        mode="recorded",
        settings=SuiteSettings(token_budget=4000000),
    )
    draft = await service.create(
        scope,
        principal,
        kind="suite",
        name="Changed limit",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    changed = await service.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    async with uow() as work:
        with pytest.raises(DatasetConflict, match="budget_namespace_changed"):
            await prepare_budget_namespace(
                work,
                service,
                scope,
                principal,
                namespace_id=identity,
                suite_version_id=changed.id,
                policy_pair=pair,
            )
        with pytest.raises(DatasetNotFound, match="budget_namespace_unavailable"):
            await work.evaluation_budget_control.namespace(
                OwnerScope.personal("another-user"), identity
            )
        saved = await work.evaluation_budget_control.namespace(scope, identity)
        assert saved.token_budget == 3000000
        assert saved.requester == principal.model_dump(mode="json")
