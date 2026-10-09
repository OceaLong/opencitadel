# ruff: noqa: F401,F811
"""Actual API role direct sends share the durable physical pools."""

import pytest
from sqlalchemy import text

from app.domain.evaluation.budget import BudgetPolicy
from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_embedding_service import _resolved_embedding
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_environment_repository import (
    environment_kernel,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


def _authorize_admin(connection):
    from app.infrastructure.security.db_authorization import configure_sync_system_authorization
    from core.config import load_deployment_settings

    configure_sync_system_authorization(
        connection,
        actor="execution-kernel",
        signing_secret=load_deployment_settings().database_authorization_signing_secret,
    )


async def test_api_direct_permit_commits_and_completion_releases_shared_slot(
    datasets, environment_kernel
):
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory,
        inventory=None,
        physical_policy=policy,
    )
    guard = dispatch.guard(
        scope, AuthorizationContext.for_principal(principal, scope=scope), purpose="inference.probe"
    )
    model = _resolved_embedding()
    permit = await guard.before_send(model, {"model": model.model_name, "input": ["hello"]})
    identity = permit.consume()
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 1
        )
    with pytest.raises(ValueError, match="concurrency_exhausted"):
        await guard.before_send(model, {"model": model.model_name, "input": ["second"]})
    await guard.after_completion(identity, None)
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )


async def test_api_original_completion_survives_revoke_but_new_send_does_not(
    datasets, environment_kernel, isolated_database
):
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    guard = dispatch.guard(
        scope, AuthorizationContext.for_principal(principal, scope=scope), purpose="inference.probe"
    )
    model = _resolved_embedding()
    identity = (await guard.before_send(model, {"model": model.model_name})).consume()
    engine, _ = isolated_database
    with engine.begin() as db:
        _authorize_admin(db)
        updated = db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        assert updated.rowcount == 1
    await guard.after_completion(identity, None)
    await guard.after_completion(identity, None)
    with pytest.raises(ValueError, match="requester_revoked"):
        await guard.before_send(model, {"model": model.model_name})


async def test_actual_embedding_service_and_probe_each_batch_is_committed_and_closed(
    datasets, environment_kernel, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.security.authorization_context import authorization_scope
    from app.application.services.embedding_service import EmbeddingService
    from app.application.services.inference_model_service import InferenceModelService
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    sends, clients = [], []

    async def send(**payload):
        async with environment_kernel() as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                )
                == 1
            )
        sends.append(payload)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[0.1] * 1536)
                for i, _ in enumerate(payload["input"])
            ],
            model_dump=lambda: {"usage": {"prompt_tokens": 2, "total_tokens": 2}},
        )

    def sdk(**kwargs):
        client = SimpleNamespace(embeddings=SimpleNamespace(create=send), close=AsyncMock())
        clients.append(client)
        return client

    monkeypatch.setattr("app.infrastructure.external.inference.embedding.AsyncOpenAI", sdk)
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    model = _resolved_embedding()
    factory = SimpleNamespace(create_embedding=OpenAICompatibleEmbedding)
    bindings = SimpleNamespace(resolve=AsyncMock(return_value=model))
    embeddings = EmbeddingService(bindings, factory, physical_dispatch=dispatch)
    probe = InferenceModelService(
        service.uow_factory, None, None, factory, physical_dispatch=dispatch
    )
    probe.resolve_model = AsyncMock(return_value=model)
    with authorization_scope(AuthorizationContext.for_principal(principal, scope=scope)):
        assert len(await embeddings.embed(["a", "b", "c"], scope=scope)) == 3
        await embeddings.embed(["a", " "], scope=scope)
        result = await probe.probe_model(model.id, scope=scope)
        assert result.status.value == "ok"
    assert len(sends) == 3
    assert len(clients) == 2
    for client in clients:
        client.close.assert_awaited_once()


async def test_actual_image_transport_accounts_wire_model_before_parse_failure(
    datasets, environment_kernel, monkeypatch
):
    from types import SimpleNamespace

    import httpx

    from app.application.security.authorization_context import authorization_scope
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.external.image_generation.provider import ProviderImageGenerator

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    seen = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            seen.append("closed")

        async def post(self, url, **kwargs):
            async with environment_kernel() as work:
                demand = await work.db_session.scalar(
                    text("SELECT demand FROM evaluation_budget_reservations")
                )
                assert demand["direct_request"]["wire_model"] == "dall-e-3"
                assert demand["direct_request"]["configured_model"] != "dall-e-3"
                assert (
                    await work.db_session.scalar(
                        text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                    )
                    == 1
                )
            return httpx.Response(200, content=b"invalid-json", request=httpx.Request("POST", url))

    monkeypatch.setattr(
        "app.infrastructure.external.image_generation.provider.create_ssrf_safe_async_client",
        lambda **kwargs: Client(),
    )
    generator = ProviderImageGenerator(physical_dispatch=dispatch)
    with authorization_scope(AuthorizationContext.for_principal(principal, scope=scope)):
        result = await generator.generate(
            "image", _resolved_embedding(), SimpleNamespace(), owner_user_id=scope.user_id
        )
    assert result is None
    assert seen == ["closed"]
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT settlement->>'money' FROM evaluation_budget_reservations")
            )
            is None
        )


@pytest.mark.parametrize("attack", ["signature", "operation", "identity", "scope", "demand"])
async def test_api_completion_authority_cannot_be_forged_or_repurposed(
    datasets, environment_kernel, attack
):
    import json
    from uuid import uuid4

    from app.domain.evaluation.budget import BudgetSettlement
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    guard = dispatch.guard(scope, auth, purpose="probe")
    model = _resolved_embedding()
    identity = (await guard.before_send(model, {"model": model.model_name})).consume()
    demand = guard.receipts[identity]
    async with service.uow_factory(auth) as work:
        if attack == "identity":
            identity = str(uuid4())
        if attack == "scope":
            demand = demand.model_copy(
                update={
                    "scope": "user:other",
                    "requester": "other",
                    "principal": demand.principal.model_copy(update={"user_id": "other"}),
                }
            )
        if attack == "demand":
            demand = demand.model_copy(update={"purpose": "unknown"})
        body, signature = await work.evaluation_budget._envelope(
            "complete_direct", identity, demand, settlement=BudgetSettlement(evidence="original")
        )
        if attack == "signature":
            signature = "0" * 64
        if attack == "operation":
            envelope = json.loads(body)
            envelope["operation"] = "reserve"
            body = json.dumps(envelope)
        with pytest.raises(ValueError, match="budget_"):
            await work.evaluation_budget.apply(body, signature)
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 1
        )


async def test_actual_embedding_cancellation_retains_committed_hold_and_disposes(
    datasets, environment_kernel, monkeypatch
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.security.authorization_context import authorization_scope
    from app.application.services.embedding_service import EmbeddingService
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )

    async def send(**kwargs):
        raise asyncio.CancelledError()

    close = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(embeddings=SimpleNamespace(create=send), close=close),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    embeddings = EmbeddingService(
        SimpleNamespace(resolve=AsyncMock(return_value=_resolved_embedding())),
        SimpleNamespace(create_embedding=OpenAICompatibleEmbedding),
        physical_dispatch=dispatch,
    )
    with authorization_scope(AuthorizationContext.for_principal(principal, scope=scope)):
        with pytest.raises(asyncio.CancelledError):
            await embeddings.embed(["first"], scope=scope)
        with pytest.raises(ValueError, match="concurrency_exhausted"):
            await embeddings.embed(["second"], scope=scope)
    assert close.await_count == 2
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations WHERE settlement IS NULL")
            )
            == 1
        )


async def test_actual_chat_probe_has_no_fake_run_or_f07_claim(
    datasets, environment_kernel, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openai.types.chat import ChatCompletion

    from app.application.security.authorization_context import authorization_scope
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.models.inference import ChatModelSettings, InferenceModelKind
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    model = _resolved_embedding()
    model = model.model_copy(
        update={
            "model": model.model.model_copy(
                update={
                    "kind": InferenceModelKind.CHAT,
                    "settings": ChatModelSettings(),
                    "model_name": "gpt-4.1",
                }
            )
        }
    )

    async def create(**kwargs):
        async with environment_kernel() as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                )
                == 1
            )
        return ChatCompletion(
            id="fake",
            created=0,
            object="chat.completion",
            model=model.model_name,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    close = AsyncMock()
    monkeypatch.setattr(
        "app.infrastructure.external.llm.openai_llm.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=close
        ),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.llm.openai_llm.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    models = InferenceModelService(
        service.uow_factory,
        None,
        SimpleNamespace(create_model_client=lambda resolved, **kwargs: OpenAILLM(resolved)),
        None,
        physical_dispatch=dispatch,
    )
    models.resolve_model = AsyncMock(return_value=model)
    with authorization_scope(AuthorizationContext.for_principal(principal, scope=scope)):
        assert (await models.probe_model(model.id, scope=scope)).status.value == "ok"
    close.assert_awaited_once()
    async with environment_kernel() as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT settlement->>'tokens' FROM evaluation_budget_reservations")
            )
            == "3"
        )


async def test_memory_pool_one_commits_permit_before_send_and_preserves_concurrent_edit(
    datasets, environment_kernel, monkeypatch, isolated_database
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.application.security.authorization_context import authorization_scope
    from app.application.services.embedding_service import EmbeddingService
    from app.application.services.memory_service import MemoryService
    from app.domain.errors import ConflictError
    from app.domain.models.memory_entry import MemoryEntry
    from app.domain.runtime_policy import MemoryExecutionPolicy
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding
    from app.infrastructure.repositories.db_uow import DBUnitOfWork

    service, scope, principal, _, _ = datasets
    prototype = service.uow_factory()
    engine = create_async_engine(
        prototype.session_factory.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=2
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def uow(auth=None):
        return DBUnitOfWork(
            sessions,
            secret_cipher=prototype._secret_cipher,
            audit_signing_key=prototype._audit_signing_key,
            audit_signing_key_id=prototype._audit_signing_key_id,
            database_authorization_signing_secret=prototype._database_authorization_signing_secret,
            authorization_context=auth,
        )

    policy = BudgetPolicy(global_concurrency=1, user_concurrency=1, provider_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=uow, inventory=None, physical_policy=policy
    )
    entry = MemoryEntry(title="first", content="body")
    race = False

    async def send(**kwargs):
        async with uow(AuthorizationContext.for_principal(principal, scope=scope)) as work:
            # Same pool-one can be checked out: neither memory nor reservation
            # transaction holds the only connection during physical HTTP.
            assert await work.db_session.scalar(text("SELECT current_user"))
        if race:
            admin, _ = isolated_database
            with admin.begin() as db:
                _authorize_admin(db)
                updated = db.execute(
                    text(
                        "UPDATE memory_entries SET content='concurrent',updated_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": entry.id},
                )
                assert updated.rowcount == 1
        return SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.1] * 1536)],
            model_dump=lambda: {"usage": {"prompt_tokens": 2, "total_tokens": 2}},
        )

    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            embeddings=SimpleNamespace(create=send), close=AsyncMock()
        ),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    embeddings = EmbeddingService(
        SimpleNamespace(resolve=AsyncMock(return_value=_resolved_embedding())),
        SimpleNamespace(create_embedding=OpenAICompatibleEmbedding),
        physical_dispatch=dispatch,
    )
    memory = MemoryService(uow, embeddings)
    try:
        with authorization_scope(AuthorizationContext.for_principal(principal, scope=scope)):
            await asyncio.wait_for(
                memory.create_entry(
                    entry, scope, policy=MemoryExecutionPolicy(vector_enabled=True)
                ),
                timeout=5,
            )
            race = True
            with pytest.raises(ConflictError):
                await asyncio.wait_for(
                    memory.update_entry(
                        entry.id,
                        MemoryEntry(content="replacement"),
                        scope,
                        policy=MemoryExecutionPolicy(vector_enabled=True),
                    ),
                    timeout=5,
                )
            assert (await memory.get_entry(entry.id, scope)).content == "concurrent"
    finally:
        await engine.dispose()


async def test_api_concurrent_workspaces_share_user_limit_without_global_read_grant(
    datasets, environment_kernel, isolated_database
):
    import asyncio
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    from app.domain.models.scope import OwnerScope
    from app.domain.models.team import TeamRole
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService

    service, personal, principal, _, _ = datasets
    team = str(uuid4())
    admin, _ = isolated_database
    with admin.begin() as db:
        _authorize_admin(db)
        db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
        db.execute(
            text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
            {"team": team, "user": principal.user_id},
        )
    principal = principal.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
    policy = BudgetPolicy(user_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    scopes = [personal, OwnerScope.team(principal.user_id, team)]
    guards = [
        dispatch.guard(
            scope, AuthorizationContext.for_principal(principal, scope=scope), purpose="probe"
        )
        for scope in scopes
    ]
    model = _resolved_embedding()
    results = await asyncio.gather(
        *(guard.before_send(model, {"model": model.model_name}) for guard in guards),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    loser = next(result for result in results if isinstance(result, ValueError))
    assert "concurrency_exhausted" in str(loser)
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=personal)
    ) as work:
        with pytest.raises(DBAPIError):
            await work.db_session.execute(text("SELECT * FROM evaluation_budget_buckets"))


async def test_api_revalidates_current_requester_after_blocking_policy_lock(
    datasets, environment_kernel, isolated_database, monkeypatch
):
    import asyncio

    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService
    from app.infrastructure.repositories.db_evaluation_budget_repository import (
        DBEvaluationBudgetRepository,
    )

    service, scope, principal, _, _ = datasets
    policy = BudgetPolicy(global_concurrency=1)
    async with environment_kernel() as work:
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=policy
    )
    guard = dispatch.guard(
        scope, AuthorizationContext.for_principal(principal, scope=scope), purpose="probe"
    )
    started, go = asyncio.Event(), asyncio.Event()
    original = DBEvaluationBudgetRepository.apply

    async def apply(repo, *args):
        started.set()
        return await original(repo, *args)

    monkeypatch.setattr(DBEvaluationBudgetRepository, "apply", apply)

    async def waiting():
        await go.wait()
        return await guard.before_send(_resolved_embedding(), {"model": "embedding"})

    task = asyncio.create_task(waiting())
    try:
        async with environment_kernel() as work:
            await work.evaluation_physical_policy.active(lock=True)
            go.set()
            await asyncio.wait_for(started.wait(), 3)
            admin, _ = isolated_database
            with admin.begin() as db:
                _authorize_admin(db)
                updated = db.execute(
                    text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                    {"id": principal.user_id},
                )
                assert updated.rowcount == 1
            await work.commit()
        result = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5))[0]
        assert isinstance(result, ValueError)
        assert "requester_revoked" in str(result)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_api_direct_send_requires_activated_policy(datasets):
    from app.infrastructure.execution.direct_physical_dispatch import DirectPhysicalDispatchService

    service, scope, principal, _, _ = datasets
    dispatch = DirectPhysicalDispatchService(
        uow_factory=service.uow_factory, inventory=None, physical_policy=BudgetPolicy()
    )
    guard = dispatch.guard(
        scope, AuthorizationContext.for_principal(principal, scope=scope), purpose="probe"
    )
    with pytest.raises(ValueError, match="policy_unavailable"):
        await guard.before_send(_resolved_embedding(), {"model": "embedding"})
