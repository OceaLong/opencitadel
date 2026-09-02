"""Real PostgreSQL proof for the complete governed Effect approval loop."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.kernel.application.command_service import CommandService
from app.kernel.application.ports import CommandResultStatus, KernelAuthorization
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.types import OwnerScopeRef, Workflow
from app.kernel.domain.workflows.agent import agent_reducer
from app.kernel.infrastructure.postgres.models import (
    KERNEL_TABLES,
    KernelApprovalReviewerORM,
    KernelApprovalViewORM,
    KernelEffectORM,
    KernelEffectViewORM,
    KernelEventORM,
    KernelNotificationViewORM,
    KernelRunViewORM,
    KernelTimerORM,
)
from app.kernel.infrastructure.postgres.store import PostgresKernelStore

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
RUN_ID = UUID(int=7200)
MODEL_EFFECT_ID = UUID(int=7201)
TOOL_EFFECT_ID = UUID(int=7202)
APPROVAL_ID = UUID(int=7203)
TIMER_ID = UUID(int=7204)
SCOPE = OwnerScopeRef.team("team-1")


@pytest_asyncio.fixture
async def approval_factory():
    uri = os.getenv("KERNEL_V2_TEST_DATABASE_URI")
    if not uri:
        pytest.skip("KERNEL_V2_TEST_DATABASE_URI is required for approval proofs")
    engine = create_async_engine(uri)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                + " RESTART IDENTITY CASCADE"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE "
                    + ", ".join(table.name for table in reversed(KERNEL_TABLES))
                    + " RESTART IDENTITY CASCADE"
                )
            )
        await engine.dispose()


def _cipher(data: dict[str, object]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _command(
    command_id: int,
    type_: str,
    payload: dict[str, object],
    *,
    actor: str,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=UUID(int=command_id),
        run_id=RUN_ID,
        workflow=Workflow.AGENT,
        type=type_,
        payload=payload,
        expected_stream_version=None,
        owner_scope=SCOPE,
        actor_user_id=actor,
        request_id=f"request-{command_id}",
        submitted_at=NOW,
    )


def _facts(command: CommandEnvelope, state) -> DecisionFacts:
    if command.type == "StartAgent":
        return DecisionFacts(
            now=NOW,
            actor_user_id=command.actor_user_id,
            request_id=command.request_id,
            policy_revision_id=UUID(int=7210),
            event_ids=(UUID(int=7211), UUID(int=7212), UUID(int=7213)),
            effect_ids=(MODEL_EFFECT_ID,),
        )
    if command.type == "EffectSucceeded":
        return DecisionFacts(
            now=NOW,
            actor_user_id=command.actor_user_id,
            request_id=command.request_id,
            policy_revision_id=UUID(int=7210),
            event_ids=tuple(UUID(int=value) for value in range(7220, 7228)),
            effect_ids=(TOOL_EFFECT_ID,),
            approval_ids=(APPROVAL_ID,),
            timer_ids=(TIMER_ID,),
            reviewer_user_ids=("team-owner", "team-admin"),
        )
    return DecisionFacts(
        now=NOW,
        actor_user_id=command.actor_user_id,
        request_id=command.request_id,
        policy_revision_id=UUID(int=7210),
        event_ids=(UUID(int=7230), UUID(int=7231), UUID(int=7232)),
    )


def _service(factory) -> CommandService:
    return CommandService(
        store=PostgresKernelStore(
            factory,
            encrypt_private=_cipher,
            decrypt_private=json.loads,
        ),
        reducers=ReducerRegistry({Workflow.AGENT: agent_reducer}),
        facts_factory=_facts,
    )


@pytest.mark.asyncio
async def test_team_approval_notifies_every_reviewer_and_releases_same_effect(
    approval_factory,
) -> None:
    service = _service(approval_factory)
    await service.submit(
        _command(
            7240,
            "StartAgent",
            {
                "prompt": "inspect",
                "tool_catalog": [
                    {
                        "name": "shell.run",
                        "safety": "non_idempotent_write",
                        "requires_approval": True,
                    }
                ],
            },
            actor="team-owner",
        ),
        KernelAuthorization.for_user("team-owner", SCOPE),
    )
    # The model worker completed the claimed Effect using fencing generation 1.
    async with approval_factory() as session, session.begin():
        await session.execute(
            update(KernelEffectORM)
            .where(KernelEffectORM.id == MODEL_EFFECT_ID)
            .values(status="started", claim_generation=1)
        )
    await service.submit(
        _command(
            7241,
            "EffectSucceeded",
            {
                "effect_id": str(MODEL_EFFECT_ID),
                "effect_type": "model.call",
                "claim_generation": 1,
                "tool_calls": [{"name": "shell.run", "arguments": {"command": "pwd"}}],
            },
            actor="kernel-effect-worker",
        ),
        KernelAuthorization.system("kernel-effect-worker", SCOPE),
    )

    async with approval_factory() as session:
        approval = await session.get(KernelApprovalViewORM, APPROVAL_ID)
        effect = await session.get(KernelEffectORM, TOOL_EFFECT_ID)
        timer = await session.get(KernelTimerORM, TIMER_ID)
        reviewers = set(
            await session.scalars(
                select(KernelApprovalReviewerORM.user_id).where(
                    KernelApprovalReviewerORM.approval_id == APPROVAL_ID
                )
            )
        )
        notification_count = await session.scalar(
            select(func.count()).select_from(KernelNotificationViewORM)
        )
    assert approval is not None
    assert approval.status == "pending"
    assert effect is not None
    assert effect.status == "blocked"
    assert timer is not None
    assert timer.status == "pending"
    assert reviewers == {"team-owner", "team-admin"}
    assert notification_count == 2

    await service.submit(
        _command(
            7242,
            "DecideApproval",
            {
                "approval_id": str(APPROVAL_ID),
                "decision": "approved",
                "feedback": "approved for this invocation",
            },
            actor="team-admin",
        ),
        KernelAuthorization.for_user("team-admin", SCOPE),
    )

    async with approval_factory() as session:
        approval = await session.get(KernelApprovalViewORM, APPROVAL_ID)
        effect = await session.get(KernelEffectORM, TOOL_EFFECT_ID)
        effect_view = await session.get(KernelEffectViewORM, TOOL_EFFECT_ID)
        timer = await session.get(KernelTimerORM, TIMER_ID)
        run = await session.get(KernelRunViewORM, RUN_ID)
    assert approval is not None
    assert (approval.status, approval.decision, approval.decided_by_user_id) == (
        "decided",
        "approved",
        "team-admin",
    )
    assert approval.feedback == "approved for this invocation"
    assert effect is not None
    assert effect.status == "ready"
    assert effect_view is not None
    assert effect_view.status == "ready"
    assert timer is not None
    assert timer.status == "cancelled"
    assert run is not None
    assert run.status == "running"


@pytest.mark.asyncio
async def test_stale_effect_outcome_is_rejected_before_reducer_or_journal_append(
    approval_factory,
) -> None:
    service = _service(approval_factory)
    await service.submit(
        _command(
            7250,
            "StartAgent",
            {"prompt": "inspect", "tool_catalog": []},
            actor="team-owner",
        ),
        KernelAuthorization.for_user("team-owner", SCOPE),
    )
    async with approval_factory() as session, session.begin():
        await session.execute(
            update(KernelEffectORM)
            .where(KernelEffectORM.id == MODEL_EFFECT_ID)
            .values(status="started", claim_generation=2)
        )

    result = await service.submit(
        _command(
            7251,
            "EffectSucceeded",
            {
                "effect_id": str(MODEL_EFFECT_ID),
                "effect_type": "model.call",
                "claim_generation": 1,
                "content": "stale",
            },
            actor="kernel-effect-worker",
        ),
        KernelAuthorization.system("kernel-effect-worker", SCOPE),
    )

    async with approval_factory() as session:
        event_count = await session.scalar(select(func.count()).select_from(KernelEventORM))
        effect = await session.get(KernelEffectORM, MODEL_EFFECT_ID)
    assert result.status is CommandResultStatus.REJECTED
    assert result.error_code == "stale_effect_claim"
    assert event_count == 3
    assert effect is not None
    assert effect.status == "started"
