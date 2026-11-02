from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.execution.context_models import ContextSourceRef
from app.domain.runtime_policy.context import ContextPolicy
from app.infrastructure.execution.context_original_reader import ContextOriginalReader


@pytest.mark.asyncio
async def test_original_read_authority_is_exact_and_dies_with_its_transaction():
    run_id = uuid4()
    source = ContextSourceRef(
        source_id=f"input:{run_id}",
        scope_key="user:original",
        run_id=run_id,
        kind="user",
        digest="a" * 64,
        formal_position=0,
        authorization_epoch="owned-epoch",
    )
    transaction = SimpleNamespace(is_active=True)
    work = SimpleNamespace(db_session=SimpleNamespace(get_transaction=lambda: transaction))
    manifest = SimpleNamespace(
        boundary=object(), policy=ContextPolicy(), original_closure=(source,)
    )
    state = SimpleNamespace(run_id=run_id, semantic_payload={"input_ref": "owned-input"})
    sources = SimpleNamespace(
        validate_in_uow=AsyncMock(),
        open=AsyncMock(return_value=(object(), "owned-epoch", state, {})),
    )
    objects = SimpleNamespace(load_input_bounded=AsyncMock(return_value={"message": "abcdef"}))
    reader = ContextOriginalReader(uow_factory=None, sources=sources, objects=objects, storage=None)
    read = await reader.bind_in_uow(work, manifest)
    assert await read(source, offset=0, limit=3) == ("abc", 3, 6)
    assert await read(source, offset=3, limit=3) == ("def", None, 6)
    sources.validate_in_uow.assert_awaited_once_with(work, manifest.boundary, (source,))
    sources.open.assert_awaited_once_with(work, manifest.boundary)
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_IDENTITY_CONFLICT"):
        await read(source.model_copy(update={"digest": "b" * 64}), offset=0, limit=3)
    transaction.is_active = False
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_AUTHORIZATION_REVOKED"):
        await read(source, offset=0, limit=3)
    assert objects.load_input_bounded.await_count == 2
