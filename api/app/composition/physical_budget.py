"""Explicit physical quota policy activation; null caps still count occupancy."""

import asyncio

from app.domain.evaluation.budget import BudgetPolicy
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
    DBEvaluationBudgetPolicyRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


def configured_physical_policy(settings):
    return BudgetPolicy(
        revision=settings.physical_budget_policy_revision,
        global_concurrency=settings.physical_global_concurrency,
        user_concurrency=settings.physical_user_concurrency,
        provider_concurrency=settings.physical_provider_concurrency,
    )


async def initialize_physical_policy(*, settings, session_factory):
    policy = configured_physical_policy(settings)
    async with session_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("execution-kernel")
        )
        await DBEvaluationBudgetPolicyRepository(session).bootstrap(policy)
        await session.commit()
    return policy


async def activate_physical_policy(*, session_factory, path, expected_revision):
    policy = BudgetPolicy.model_validate_json(await asyncio.to_thread(path.read_text))
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("budget_policy_changed")
    async with session_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("execution-kernel")
        )
        repo = DBEvaluationBudgetPolicyRepository(session)
        if expected_revision == 0:
            if policy.revision != 1:
                raise ValueError("budget_policy_changed")
            await repo.bootstrap(policy)
        else:
            await repo.activate(policy, expected_revision=expected_revision)
        await session.commit()
    return policy


async def verify_physical_policy(*, settings, session_factory):
    """API reads authority; kernel/operator owns initial activation and changes."""
    expected = configured_physical_policy(settings)
    async with session_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("physical-policy-reader")
        )
        current = await DBEvaluationBudgetPolicyRepository(session).active()
        if current is not None and current != expected:
            raise ValueError("budget_policy_changed")
        return current
