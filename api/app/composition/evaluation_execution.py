"""Deployment policy startup and explicit operator activation for Run slots."""

import asyncio
from pathlib import Path

from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
from app.infrastructure.repositories.db_evaluation_execution_repository import (
    DBEvaluationExecutionRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization


def configured_execution_policy(settings):
    return ExecutionSlotPolicy(
        revision=settings.evaluation_execution_policy_revision,
        subject_limit=settings.evaluation_subject_concurrency,
        judge_limit=settings.evaluation_judge_concurrency,
        global_limit=settings.evaluation_execution_global_limit,
        user_limit=settings.evaluation_execution_user_limit,
    )


async def build_evaluation_execution(*, settings, session_factory):
    policy = configured_execution_policy(settings)
    authorization = AuthorizationContext.system("execution-kernel")
    async with session_factory() as session:
        await configure_session_authorization(session, authorization)
        await DBEvaluationExecutionRepository(session).bootstrap(policy)
        await session.commit()
    return EvaluationExecutionGuard(
        policy, session_factory=session_factory, authorization=authorization
    )


async def activate_execution_policy(*, session_factory, path: Path, expected_revision: int):
    policy = ExecutionSlotPolicy.model_validate_json(await asyncio.to_thread(path.read_text))
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("execution_policy_changed")
    async with session_factory() as session:
        await configure_session_authorization(
            session, AuthorizationContext.system("execution-kernel")
        )
        repo = DBEvaluationExecutionRepository(session)
        if expected_revision == 0:
            if policy.revision != 1:
                raise ValueError("execution_policy_changed")
            await repo.bootstrap(policy)
        else:
            await repo.activate(policy, expected_revision=expected_revision)
        await session.commit()
    return policy
