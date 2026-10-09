# ruff: noqa: F401,F811
"""Versioned physical caps preserve counted unknown occupancy."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_environment_repository import (
    environment_kernel,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_uncapped_calls_are_counted_and_activation_cannot_reset_holds(
    datasets, environment_kernel
):
    from app.application.evaluation.budget_service import BudgetDemandFactory
    from app.domain.evaluation.budget import BudgetPolicy, BudgetSettlement
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
        DBEvaluationBudgetPolicyRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import repository

    _, scope, principal, _, _ = datasets
    uncapped = BudgetPolicy(revision=1)
    async with environment_kernel() as work:
        await DBEvaluationBudgetPolicyRepository(work.db_session).bootstrap(uncapped)
        await work.commit()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    demand = BudgetDemandFactory(uncapped).physical(
        auth, endpoint_id="unknown", tokens=None, money=None
    )
    identities = [str(uuid4()), str(uuid4())]
    async with environment_kernel() as work:
        for identity in identities:
            await repository(work).reserve(identity, demand)
            await repository(work).mark_unknown(identity, demand)
        await work.commit()
    capped = BudgetPolicy(
        revision=2, global_concurrency=1, user_concurrency=1, provider_concurrency=1
    )
    async with environment_kernel() as work:
        await DBEvaluationBudgetPolicyRepository(work.db_session).activate(
            capped, expected_revision=1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 2
        )
        await work.commit()
    async with environment_kernel() as work:
        with pytest.raises(ValueError, match="budget_policy_changed"):
            await repository(work).reserve(str(uuid4()), demand)
    revised = BudgetDemandFactory(capped).physical(
        auth, endpoint_id="unknown", tokens=None, money=None
    )
    async with environment_kernel() as work:
        with pytest.raises(ValueError, match="budget_concurrency_exhausted"):
            await repository(work).reserve(str(uuid4()), revised)
    async with environment_kernel() as work:
        for identity in identities:
            await repository(work).settle(
                identity, demand, BudgetSettlement(tokens=0, evidence=identity)
            )
        await work.commit()
    async with environment_kernel() as work:
        assert (await repository(work).reserve(str(uuid4()), revised))["fresh"]
        await work.commit()


async def test_published_suite_preflight_requires_active_positive_physical_caps(configurations):
    from app.application.evaluation.preflight import PreflightService
    from app.domain.evaluation.budget import BudgetPolicy
    from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
        DBEvaluationBudgetPolicyRepository,
    )
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_budget_publication import (
        native_inventory,
    )
    from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
        published_suite,
    )

    service, _, scope, principal = configurations
    await native_inventory(configurations)
    suite, _ = await published_suite(configurations)
    async with execution_admin_session() as db:
        repo = DBEvaluationBudgetPolicyRepository(db)
        current = await repo.active()
        if current is None:
            await repo.bootstrap(BudgetPolicy(revision=1))
        else:
            await repo.activate(
                BudgetPolicy(revision=current.revision + 1), expected_revision=current.revision
            )
        await db.commit()
    result = await PreflightService(service, principal).check(scope, suite.id)
    assert "budget_physical_policy_unavailable" in result.errors


async def test_operator_physical_activation_requires_revision_and_restart_match(
    datasets, environment_kernel, tmp_path
):
    from app.composition.physical_budget import activate_physical_policy, initialize_physical_policy
    from app.domain.evaluation.budget import BudgetPolicy
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import authenticated_session_factory

    settings = load_deployment_settings()
    factory = authenticated_session_factory(
        environment_kernel().session_factory.kw["bind"],
        signing_secret=settings.database_authorization_signing_secret,
    )
    assert await initialize_physical_policy(
        settings=settings, session_factory=factory
    ) == BudgetPolicy(revision=1)
    path = tmp_path / "physical.json"
    policy = BudgetPolicy(
        revision=2, global_concurrency=4, user_concurrency=2, provider_concurrency=3
    )
    path.write_text(policy.model_dump_json())
    assert (
        await activate_physical_policy(session_factory=factory, path=path, expected_revision=1)
        == policy
    )
    assert (
        await activate_physical_policy(session_factory=factory, path=path, expected_revision=1)
        == policy
    )
    with pytest.raises(ValueError, match="budget_policy_changed"):
        await initialize_physical_policy(settings=settings, session_factory=factory)
    updated = settings.model_copy(
        update={
            "physical_budget_policy_revision": 2,
            "physical_global_concurrency": 4,
            "physical_user_concurrency": 2,
            "physical_provider_concurrency": 3,
        }
    )
    assert await initialize_physical_policy(settings=updated, session_factory=factory) == policy


async def test_api_startup_reads_active_policy_without_operator_privileges(configurations):
    from app.composition.physical_budget import verify_physical_policy
    from core.config import load_deployment_settings
    from tests.app.infrastructure.repositories.test_evaluation_budget_publication import (
        native_inventory,
    )

    await native_inventory(configurations)
    service = configurations[0]
    settings = load_deployment_settings().model_copy(
        update={
            "physical_global_concurrency": 10,
            "physical_user_concurrency": 10,
            "physical_provider_concurrency": 10,
        }
    )
    policy = await verify_physical_policy(
        settings=settings, session_factory=service.uow_factory().session_factory
    )
    assert policy.revision == 1
    with pytest.raises(ValueError, match="budget_policy_changed"):
        await verify_physical_policy(
            settings=settings.model_copy(update={"physical_provider_concurrency": None}),
            session_factory=service.uow_factory().session_factory,
        )
