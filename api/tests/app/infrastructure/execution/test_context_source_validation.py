"""A source cache cannot weaken the authority required by a later source."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.execution.context_models import ContextBoundary, ContextSourceRef
from app.domain.runtime_policy.context import ContextPolicy
from app.infrastructure.execution.context_source_validation import PostgresContextSourceValidator


@pytest.mark.asyncio
@pytest.mark.parametrize("user_first", [True, False])
async def test_failed_run_user_input_does_not_authorize_derived_sources(monkeypatch, user_first):
    current, previous = uuid4(), uuid4()
    scope = SimpleNamespace(user_id="owner", team_id=None)
    boundary = ContextBoundary(
        scope_key="user:owner",
        collection_id="session",
        through_position=100,
        current_run_id=current,
        current_input_digest="a" * 64,
    )
    state = SimpleNamespace(
        run_id=current,
        definition_manifest=SimpleNamespace(context_policy=ContextPolicy()),
    )
    user = ContextSourceRef(
        source_id=f"formal:{uuid4()}",
        scope_key=boundary.scope_key,
        run_id=previous,
        kind="user",
        digest="b" * 64,
        formal_position=10,
        authorization_epoch="epoch",
    )
    dependency = user.model_copy(update={"source_id": f"input:{previous}", "formal_position": 0})
    validator = PostgresContextSourceValidator(uow_factory=None, storage=None)
    validator.open = AsyncMock(return_value=(scope, "epoch", state, {}))
    calls = []

    async def source_state(_work, _scope, _boundary, identity, *, require_completed=True):
        assert identity == previous
        calls.append(require_completed)
        if require_completed:
            raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        return SimpleNamespace(
            run_id=previous, semantic_payload={"input_ref": "previous", "input_digest": user.digest}
        )

    validator.source_state = source_state
    validator.objects.load_input_bounded = AsyncMock(return_value={"message": "old user input"})
    monkeypatch.setattr(
        "app.infrastructure.execution.context_source_validation.ContextSourceDescriptors.resolve",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "app.infrastructure.execution.context_source_validation.ContextSourceReader.read",
        AsyncMock(return_value=SimpleNamespace(ref=user)),
    )
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_BOUNDARY_MISMATCH"):
        await validator.validate_in_uow(
            SimpleNamespace(db_session=None),
            boundary,
            (user, dependency) if user_first else (dependency, user),
        )
    assert calls == ([False, True] if user_first else [True])
    validator.objects.load_input_bounded.assert_not_awaited()
