# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Cancelled and abandoned operations remain original, protected Session evidence."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.execution.context_models import ContextBoundary
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.context_session_effects import session_effect_obligations
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.interfaces.endpoints.test_effect_api_postgres import effect_http

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize(
    "effect_http",
    [{"source_entity_type": "session", "source_entity_id": "history-effects"}],
    indirect=True,
)
async def test_prior_unknown_operation_survives_cancel_and_immutable_boundary(
    effect_http, datasets
):
    value = effect_http
    auth = AuthorizationContext.system("execution-kernel")
    async with value.sessions() as session:
        await configure_session_authorization(session, auth)
        work = SimpleNamespace(db_session=session)
        position = await work.db_session.scalar(text("SELECT max(position) FROM execution_events"))
    # Real aggregate cancellation preserves the unknown operation, even after its
    # Activity has become terminal. The later turn must not mistake it for failure.
    await value.send("CancelRun", {"reason": "continue in a later message"})
    state = SimpleNamespace(
        run_id=uuid4(),
        owner_user_id=value.scope.user_id,
        team_id=None,
        source_entity_type="session",
        source_entity_id="history-effects",
    )
    boundary = ContextBoundary(
        scope_key=f"user:{value.scope.user_id}",
        collection_id=state.source_entity_id,
        through_position=position,
        current_run_id=state.run_id,
        current_input_digest="a" * 64,
    )
    async with value.sessions() as session:
        await configure_session_authorization(session, auth)
        work = SimpleNamespace(db_session=session)
        sources = await session_effect_obligations(work, state, boundary, epoch="owned-live")
        assert len(sources) == 1
        source = sources[0]
        assert source.protected
        assert source.ref.run_id == value.spec.run_id
        assert str(value.spec.operation_id) in source.messages[0].content
        assert str(value.spec.invocation_id) in source.messages[0].content
        assert '"state":"unknown"' in source.messages[0].content
        assert value.spec.contract_digest in source.messages[0].content
        assert source.ref.source_id.endswith(
            ":"
            + str(
                await work.db_session.scalar(
                    text(
                        "SELECT max(stream_version) FROM execution_events WHERE stream_id=:run AND position<=:cut"
                    ),
                    {"run": str(value.spec.run_id), "cut": position},
                )
            )
        )
        other = boundary.model_copy(update={"collection_id": "another-session"})
        assert await session_effect_obligations(work, state, other, epoch="owned-live") == ()
        early = boundary.model_copy(update={"through_position": 0})
        assert await session_effect_obligations(work, state, early, epoch="owned-live") == ()
