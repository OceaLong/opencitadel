import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.infrastructure.external.llm.base_llm import normalize_usage
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_artifact_provenance_postgres import production_run
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


async def test_physical_dispatches_allocate_before_projection_and_dedupe_settlement():
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    _, producer, _, _ = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=CURRENT_TIMESTAMP+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        config = await DBExecutionUsageRepository(db).snapshot(
            producer.scope, producer.run_id, {"fixed": "snapshot"}, "production"
        )
        assert not await db.scalar(
            text("SELECT 1 FROM execution_view_runs WHERE run_id=:run"), {"run": producer.run_id}
        )
        await db.commit()

    async def allocate():
        async with execution_admin_session() as db:
            identity = await DBExecutionUsageRepository(db).allocate(
                producer.scope,
                run_id=producer.run_id,
                activity_id=producer.activity_id,
                generation=0,
                claim_generation=1,
                configuration_id=config,
                request_snapshot={"model": "alias"},
            )
            await db.commit()
            return identity

    calls = await asyncio.gather(allocate(), allocate())
    assert calls[0] != calls[1]
    fact = {"call_identity": calls[1], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}

    async def settle():
        async with execution_admin_session() as db:
            result = await DBExecutionUsageRepository(db).record(producer.scope, calls[1], fact)
            await db.commit()
            return result

    assert await asyncio.gather(settle(), settle()) == [fact, fact]
    async with execution_admin_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT d.call_identity,s.fact FROM execution_model_dispatches d LEFT JOIN execution_model_settlements s USING(scope_key,call_identity) WHERE d.run_id=:run"
                ),
                {"run": producer.run_id},
            )
        ).all()
        assert len(rows) == 2
        assert sum(row.fact is None for row in rows) == 1
        with pytest.raises(ValueError, match="conflicting"):
            await DBExecutionUsageRepository(db).record(
                producer.scope, calls[1], {**fact, "different": True}
            )


async def test_untrusted_usage_receipt_command_is_rejected():
    from app.domain.execution.commands import CommandEnvelope

    _, producer, _, handler = await production_run(activity_type="model.call")
    from datetime import UTC, datetime

    result = await handler.handle(
        CommandEnvelope(
            command_id=uuid4(),
            command_type="RecordModelUsage",
            command_schema_version=1,
            stream_type="run",
            stream_id=str(producer.run_id),
            owner_user_id=producer.scope.user_id,
            team_id=None,
            correlation_id=producer.run_id,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload={"call_identity": str(uuid4()), "phase": "dispatch"},
        )
    )
    assert result.rejection_code == "INVALID_TRANSITION"


async def test_unknown_then_late_settlement_publishes_new_cut_after_terminal():
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_execution_usage import ExecutionUsageMaintenance
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    _, producer, command, handler = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=CURRENT_TIMESTAMP+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        repo = DBExecutionUsageRepository(db)
        config = await repo.snapshot(
            producer.scope,
            producer.run_id,
            {"price_revision": "fixed", "settings": {}, "tools": {}, "prompt": {}},
            "evaluation_judge",
        )
        identity = await repo.allocate(
            producer.scope,
            run_id=producer.run_id,
            activity_id=producer.activity_id,
            generation=0,
            claim_generation=1,
            configuration_id=config,
            request_snapshot={},
        )
        await db.commit()
    maintenance = ExecutionUsageMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f07-test"),
        handler=handler,
    )
    projector = PostgresFormalProjector(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f07-test"),
    )
    await maintenance.process_pending()
    await projector.run_once(producer.scope, limit=1000)
    async with execution_admin_session() as db:
        old = (
            await db.execute(
                text(
                    "SELECT observed_order,public_payload FROM execution_view_observations WHERE run_id=:run ORDER BY observed_order DESC LIMIT 1"
                ),
                {"run": producer.run_id},
            )
        ).one()
        old_payload = old.public_payload
        fact = (
            (
                await db.execute(
                    text("SELECT * FROM execution_usage_facts WHERE call_identity=:id"),
                    {"id": identity},
                )
            )
            .mappings()
            .one()
        )
        assert fact["input_tokens"] is None
        assert fact["purpose"] == "evaluation_judge"
    await command("CancelRun", {"reason": "test"})
    async with execution_admin_session() as db:
        fact = {
            "call_identity": identity,
            "usage": normalize_usage({"prompt_tokens": 10, "completion_tokens": 2}),
            "model_revision": "actual-2026",
            "price_revision": "fixed",
            "configuration_id": config,
            "cost_usd": "0.0001",
            "version_unpinned": False,
        }
        await DBExecutionUsageRepository(db).record(producer.scope, identity, fact)
        await db.commit()
    await maintenance.process_pending()
    await maintenance.process_pending()
    await projector.run_once(producer.scope, limit=1000)
    async with execution_admin_session() as db:
        current = (
            (
                await db.execute(
                    text("SELECT * FROM execution_usage_facts WHERE call_identity=:id"),
                    {"id": identity},
                )
            )
            .mappings()
            .one()
        )
        assert current["input_tokens"] == 10
        assert current["output_tokens"] == 2
        assert (
            await db.scalar(
                text(
                    "SELECT public_payload FROM execution_view_observations WHERE run_id=:run AND observed_order=:cut"
                ),
                {"run": producer.run_id, "cut": old.observed_order},
            )
            == old_payload
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM execution_events WHERE stream_id=:run AND event_type='ModelUsageRecorded'"
                ),
                {"run": str(producer.run_id)},
            )
            == 2
        )


@pytest.fixture(autouse=True)
def fresh_f07_database(isolated_database, monkeypatch):  # noqa: F811
    from alembic import command
    from core.config import load_deployment_settings

    engine, config = isolated_database
    command.upgrade(config, "head")
    settings = load_deployment_settings()
    replacement = settings.model_copy(
        update={
            "sqlalchemy_migration_database_uri": engine.url.render_as_string(hide_password=False)
        }
    )
    monkeypatch.setattr(
        "tests.app.execution_test_support.load_deployment_settings", lambda: replacement
    )


async def test_handler_captures_actual_resolved_prompt_and_each_adapter_fallback():
    from contextlib import asynccontextmanager

    import httpx

    from app.application.execution.activities.model_call import ModelCallActivityHandler
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.domain.execution.activity import ActivityContext
    from app.domain.models.inference import InferenceProvider
    from app.domain.models.skill import Skill
    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )
    from tests.app.application.execution.test_conversation_activities import (
        Catalog,
        Objects,
        request,
    )
    from tests.app.execution_test_support import run_execution_context_for
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    _, producer, _, _ = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=CURRENT_TIMESTAMP+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        await db.commit()

    @asynccontextmanager
    async def repositories():
        async with execution_admin_session() as db:
            yield DBExecutionUsageRepository(db)
            await db.commit()

    model = resolved_chat_model(
        provider=InferenceProvider.ANTHROPIC,
        model_name="configured-alias",
        base_url="https://api.anthropic.com",
    )
    model.model.input_price_per_million = 2.0
    admitted_config = await ExecutionUsageService(
        repository_context=repositories
    ).snapshot_run_configuration(
        producer.scope, producer.run_id, model, "admitted-tools", "admitted-policy"
    )
    model.model.input_price_per_million = 9.0

    class Models:
        async def resolve_chat(self, *args, **kwargs):
            return model

    class Skills:
        async def get_skill(self, *args, **kwargs):
            return Skill(id="fixed-skill", body="resolved skill text")

    objects = Objects()
    objects.input.update(
        {
            "_execution_usage": {"purpose": "production", "configuration_id": admitted_config},
            "skill_id": "fixed-skill",
            "temperature_override": 0.2,
            "conversation": [{"role": "user", "content": "fixed prior"}],
        }
    )

    class Transport:
        count = 0

        async def aclose(self):
            pass

        async def post(self, *args, **kwargs):
            self.count += 1
            if self.count == 1:
                return httpx.Response(
                    400,
                    request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                    json={"error": {"message": "cache failure"}},
                )
            return httpx.Response(
                200,
                json={
                    "model": "provider-revision",
                    "content": [{"type": "text", "text": "answer"}],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 30,
                    },
                },
            )

    transport = Transport()
    adapter = AnthropicLLM(model)
    await adapter._client.aclose()
    adapter._client = transport
    handler = ModelCallActivityHandler(
        objects=objects,
        models=Models(),
        tools=Catalog(),
        skills=Skills(),
        execution_usage=ExecutionUsageService(repository_context=repositories),
        client_factory=lambda *args, **kwargs: adapter,
    )
    context = ActivityContext(
        worker_id="test",
        owner_user_id=producer.scope.user_id,
        team_id=None,
        idempotency_key=str(producer.activity_id),
        claim_generation=1,
        run=run_execution_context_for(
            "agent", run_id=producer.run_id, owner_user_id=producer.scope.user_id
        ),
    )
    req = request(
        "model.call",
        input_payload={
            "allow_tools": False,
            "history_refs": ["result://old-model", "result://old-tool"],
            "round": 0,
        },
    ).model_copy(update={"activity_id": producer.activity_id})
    outcome = await handler.execute(req, context)
    assert outcome.status == "succeeded"
    async with execution_admin_session() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT d.*,s.fact,c.body,c.purpose FROM execution_model_dispatches d JOIN execution_configurations c ON c.id=d.configuration_id AND c.scope_key=d.scope_key LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity WHERE d.run_id=:run ORDER BY ordinal"
                    ),
                    {"run": producer.run_id},
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == transport.count == 2
        # The failed cache attempt has no final usage statement. This local
        # usage-only guard leaves it unknown instead of inventing zero billing.
        assert rows[0]["fact"] is None
        assert rows[1]["fact"]["usage"]["prompt_tokens"] == 100
        assert rows[1]["fact"]["model_revision"] == "provider-revision"
        assert rows[1]["purpose"] == "production"
        assert rows[1]["body"]["settings"]["temperature"] == 0.2
        assert rows[1]["body"]["price"]["input_per_million"] == "2.0"
        assert "resolved skill text" in str(rows[1]["body"]["prompt"])
        assert "fixed prior" in str(rows[1]["body"]["prompt"])
        assert "I will search" in str(rows[1]["body"]["prompt"])
        assert rows[1]["body"]["skill"]["id"] == "fixed-skill"


async def test_kernel_rls_requires_signed_scope_and_immutable_ledger(
    fresh_f07_database,
    isolated_database,  # noqa: F811
):
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import Principal
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_kernel_database_uri

    admin, _ = isolated_database
    _, producer, _, _ = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=CURRENT_TIMESTAMP+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        await db.commit()
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=admin.url.database)
    )
    sessions = async_sessionmaker(
        engine,
        info={
            "database_authorization_signing_secret": load_deployment_settings().database_authorization_signing_secret
        },
    )
    try:
        async with sessions() as db:
            assert not await db.scalar(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
            assert await db.scalar(text("SELECT count(*) FROM execution_configurations")) == 0
            await configure_session_authorization(db, AuthorizationContext.system("f07-kernel"))
            repo = DBExecutionUsageRepository(db)
            config = await repo.snapshot(
                producer.scope, producer.run_id, {"fixed": "safe"}, "production"
            )
            identity = await repo.allocate(
                producer.scope,
                run_id=producer.run_id,
                activity_id=producer.activity_id,
                generation=0,
                claim_generation=1,
                configuration_id=config,
                request_snapshot={},
            )
            await db.commit()
        async with sessions() as db:
            await configure_session_authorization(
                db, AuthorizationContext.for_principal(Principal(user_id="different-owner"))
            )
            assert await db.scalar(text("SELECT count(*) FROM execution_model_dispatches")) == 0
        async with sessions() as db:
            await configure_session_authorization(db, AuthorizationContext.system("f07-kernel"))
            with pytest.raises(DBAPIError):
                await db.execute(
                    text("DELETE FROM execution_model_dispatches WHERE call_identity=:id"),
                    {"id": identity},
                )
        async with execution_admin_session() as db:
            with pytest.raises(DBAPIError, match="immutable"):
                await db.execute(
                    text("UPDATE execution_configurations SET body='{}' WHERE id=:id"),
                    {"id": config},
                )
    finally:
        await engine.dispose()


async def test_settlement_rejects_negative_or_wrong_configuration_evidence():
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    _, producer, _, _ = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=CURRENT_TIMESTAMP+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        repo = DBExecutionUsageRepository(db)
        config = await repo.snapshot(
            producer.scope, producer.run_id, {"price_revision": "fixed"}, "production"
        )
        identity = await repo.allocate(
            producer.scope,
            run_id=producer.run_id,
            activity_id=producer.activity_id,
            generation=0,
            claim_generation=1,
            configuration_id=config,
            request_snapshot={},
        )
        await db.commit()
    async with execution_admin_session() as db:
        with pytest.raises(ValueError, match="invalid usage"):
            await DBExecutionUsageRepository(db).record(
                producer.scope,
                identity,
                {"call_identity": identity, "usage": {"prompt_tokens": -1}},
            )
    async with execution_admin_session() as db:
        with pytest.raises(ValueError, match="configuration"):
            await DBExecutionUsageRepository(db).record(
                producer.scope,
                identity,
                {
                    "call_identity": identity,
                    "configuration_id": "wrong",
                    "usage": {"prompt_tokens": 1},
                },
            )


async def test_early_settlement_cannot_retrofill_dispatch_cut_and_new_claim_is_distinct():
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_execution_usage import ExecutionUsageMaintenance
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    _, producer, _, handler = await production_run(activity_type="model.call")
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_deadline=CURRENT_TIMESTAMP+INTERVAL '1 hour' WHERE activity_id=:id"
            ),
            {"id": producer.activity_id},
        )
        repo = DBExecutionUsageRepository(db)
        config = await repo.snapshot(producer.scope, producer.run_id, {}, "production")
        kwargs = {
            "run_id": producer.run_id,
            "activity_id": producer.activity_id,
            "generation": 0,
            "configuration_id": config,
            "request_snapshot": {},
        }
        first = await repo.allocate(producer.scope, claim_generation=1, **kwargs)
        await repo.record(
            producer.scope,
            first,
            {
                "call_identity": first,
                "usage": normalize_usage({"prompt_tokens": 12, "completion_tokens": 3}),
            },
        )
        await db.execute(
            text("UPDATE execution_activity_tasks SET claim_generation=2 WHERE activity_id=:id"),
            {"id": producer.activity_id},
        )
        second = await repo.allocate(producer.scope, claim_generation=2, **kwargs)
        assert first != second
        with pytest.raises(ValueError, match="claim unavailable"):
            await repo.allocate(producer.scope, claim_generation=1, **kwargs)
        await db.commit()
    maintenance = ExecutionUsageMaintenance(
        session_factory=execution_admin_session,
        authorization=AuthorizationContext.system("f07"),
        handler=handler,
    )
    await maintenance.process_pending()
    await maintenance.process_pending()
    projector = PostgresFormalProjector(
        session_factory=execution_admin_session, authorization=AuthorizationContext.system("f07")
    )
    await projector.run_once(producer.scope, limit=1000)
    async with execution_admin_session() as db:
        patches = (
            await db.execute(
                text(
                    "SELECT p.phase,o.public_payload FROM execution_usage_publications p JOIN execution_view_observations o ON o.event_id=p.event_id WHERE p.call_identity=:id ORDER BY o.observed_order"
                ),
                {"id": first},
            )
        ).all()
        assert len(patches) == 2
        first_usage = patches[0].public_payload["facts"][-1]["patch"]["usage"]["production"]
        last_usage = patches[1].public_payload["facts"][-1]["patch"]["usage"]["production"]
        assert first_usage["known_input_count"] == 0
        assert first_usage["unknown_usage_calls"] == first_usage["calls"]
        assert last_usage["known_input_count"] == 12
        assert last_usage["unknown_usage_calls"] == 1
