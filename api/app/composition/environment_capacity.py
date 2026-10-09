"""Versioned environment capacity activation; E04 leases retain actual occupancy."""

import asyncio

from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_environment_capacity_repository import (
    DBEnvironmentCapacityRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


def configured_environment_capacity(settings):
    return EnvironmentCapacityPolicy(
        revision=settings.evaluation_environment_policy_revision,
        workspace_limit=settings.evaluation_environment_concurrency,
        global_limit=settings.evaluation_environment_global_limit,
        user_limit=settings.evaluation_environment_user_limit,
    )


async def initialize_environment_capacity(*, settings, session_factory):
    policy = configured_environment_capacity(settings)
    async with session_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("execution-kernel")
        )
        await DBEnvironmentCapacityRepository(session).bootstrap(policy)
        await session.commit()
    return policy


async def activate_environment_capacity(*, session_factory, path, expected_revision):
    policy = EnvironmentCapacityPolicy.model_validate_json(await asyncio.to_thread(path.read_text))
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("environment_capacity_policy_changed")
    async with session_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("execution-kernel")
        )
        repo = DBEnvironmentCapacityRepository(session)
        if expected_revision == 0:
            if policy.revision != 1:
                raise ValueError("environment_capacity_policy_changed")
            await repo.bootstrap(policy)
        else:
            await repo.activate(policy, expected_revision=expected_revision)
        await session.commit()
    return policy
