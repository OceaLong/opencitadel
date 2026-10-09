# ruff: noqa: F401, F811
"""Published suite evidence through real E02 ordinary-role transactions."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.execution_test_support import execution_admin_session
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
    published_suite,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def native_inventory(configurations):
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.domain.evaluation.budget_capabilities import BudgetInventory

    service, _, _, _ = configurations
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE inference_endpoints SET base_url='https://api.openai.com/v1',credential='metadata-presence-only' WHERE id='e02-endpoint'"
            )
        )
        await db.execute(
            text("UPDATE inference_models SET model_name='gpt-4.1-2025-04-14' WHERE id='e02-model'")
        )
        await db.execute(
            text(
                "INSERT INTO inference_models(id,endpoint_id,display_name,model_name,kind,settings) SELECT 'e05-fallback',endpoint_id,'fallback',model_name,kind,settings FROM inference_models WHERE id='e02-model'"
            )
        )
        await db.commit()
    inventory = BudgetInventory.model_validate(
        {
            "revision": "test-native-1",
            "endpoints": {
                "e02-endpoint": {
                    "provider": "openai",
                    "origin": "https://api.openai.com/v1",
                    "pool": "native-account",
                }
            },
            "profiles": [
                {
                    "endpoint_id": "e02-endpoint",
                    "model": "gpt-4.1-2025-04-14",
                    "profile": "openai-gpt41-chat-v1",
                }
            ],
        }
    )
    from app.domain.evaluation.budget import BudgetPolicy
    from app.infrastructure.repositories.db_evaluation_budget_policy_repository import (
        DBEvaluationBudgetPolicyRepository,
    )

    async with execution_admin_session() as db:
        await DBEvaluationBudgetPolicyRepository(db).bootstrap(
            BudgetPolicy(
                revision=1, global_concurrency=10, user_concurrency=10, provider_concurrency=10
            )
        )
        await db.commit()
    service.budgets = BudgetAuthority(inventory)
    return inventory


async def test_real_publication_pins_all_candidates_without_decrypt_and_preflight_borrows_pool_one(
    configurations, monkeypatch
):
    from app.application.evaluation.preflight import PreflightService
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.postgres_runtime_policy_repository import (
        PostgresRuntimePolicyRepository,
    )
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher

    inventory = await native_inventory(configurations)
    service, _, scope, principal = configurations

    def forbidden(*args, **kwargs):
        raise AssertionError("metadata must not resolve secrets or discover providers")

    monkeypatch.setattr(ApiKeyCipher, "decrypt_versioned", forbidden)
    monkeypatch.setattr(InferenceModelService, "list_resolved_chat_models", forbidden)
    original = service.uow_factory
    original_factory = original().session_factory
    engine = create_async_engine(original_factory.kw["bind"].url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(
        engine, expire_on_commit=False, info=original_factory.kw.get("info", {})
    )

    def single(authorization_context=None):
        uow = original(authorization_context)
        uow.session_factory = factory
        return uow

    service.uow_factory = single
    service.policies = PostgresRuntimePolicyRepository(
        session_factory=factory, authorization=AuthorizationContext.system("runtime-policy-reader")
    )
    try:
        suite, subject = await asyncio.wait_for(published_suite(configurations), timeout=5)
        proof = subject.snapshot.get("budget")
        assert proof is not None, "published configuration must capture deployment authority"
        assert proof["inventory"] == inventory.fingerprint
        assert [c["identity"]["model_id"] for c in proof["candidates"]] == [
            "e02-model",
            "e05-fallback",
        ]
        assert "https://api.openai.com" not in subject.model_dump_json()
        checked = await asyncio.wait_for(
            PreflightService(service, principal).check(scope, suite.id), timeout=3
        )
        assert "budget_single_call_exceeds_limit" in checked.errors
        assert "budget_authority_unavailable" not in checked.errors
        pair = await service.policies.load_active_pair()
        monkeypatch.setattr(service.policies, "load_active_pair", forbidden)
        async with single(service.auth(scope, principal)) as uow:
            borrowed = await asyncio.wait_for(
                PreflightService(service, principal).revalidate_for_start(
                    scope, suite.id, uow=uow, policy_pair=pair
                ),
                timeout=3,
            )
            assert borrowed.evidence["budget"] == checked.evidence["budget"]
            assert (
                borrowed.evidence["budget"] != inventory.fingerprint
            )  # includes active physical policy
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "UPDATE inference_models SET settings=jsonb_set(settings,'{temperature}','0.9') WHERE id='e05-fallback'"
                )
            )
            await db.commit()
        monkeypatch.undo()
        drift = await PreflightService(service, principal).check(scope, suite.id)
        assert "budget_candidates_changed" in drift.errors
    finally:
        await engine.dispose()


async def test_unsupported_permitted_fallback_blocks_publication_and_old_proof_remains_unavailable(
    configurations,
):
    from app.application.evaluation.preflight import PreflightService
    from app.domain.evaluation.configuration import ConfigSelection

    service, _, scope, principal = configurations
    old_suite, old_subject = await published_suite(configurations)
    await native_inventory(configurations)
    assert "budget" not in old_subject.snapshot
    checked = await PreflightService(service, principal).check(scope, old_suite.id)
    assert "budget_configuration_proof_unavailable" in checked.errors
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE inference_models SET model_name='unregistered-fallback' WHERE id='e05-fallback'"
            )
        )
        await db.commit()
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Unsupported fallback",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    with pytest.raises(ValueError, match="budget_capability_unavailable"):
        await service.publish(
            scope,
            principal,
            kind="config",
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )


async def test_repeated_publication_proof_and_fixed_price_coverage(configurations):
    from app.application.evaluation.budget_service import BudgetAuthority
    from app.application.evaluation.preflight import PreflightService
    from app.domain.evaluation.budget_capabilities import BudgetInventory
    from app.domain.evaluation.configuration import ConfigSelection, SuiteDefinition, SuiteSettings

    inventory = await native_inventory(configurations)
    service, _, scope, principal = configurations
    suite, subject = await published_suite(configurations)
    draft = await service.create(
        scope,
        principal,
        kind="config",
        name="Same proof",
        definition=ConfigSelection(model_id="e02-model").model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    repeated = await service.publish(
        scope,
        principal,
        kind="config",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    assert repeated.snapshot["budget"] == subject.snapshot["budget"]
    money_definition = SuiteDefinition(
        dataset_version=suite.dataset_version,
        config_versions=suite.config_versions,
        rubric_version=suite.rubric_version,
        mode="recorded",
        settings=SuiteSettings(token_budget=3000000, money_budget=100),
    )
    draft = await service.create(
        scope,
        principal,
        kind="suite",
        name="Money",
        definition=money_definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    money_suite = await service.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    checked = await PreflightService(service, principal).check(scope, money_suite.id)
    assert "budget_price_coverage_incomplete" in checked.errors
    assert "budget_single_call_exceeds_limit" not in checked.errors
    changed = inventory.model_dump(mode="json")
    changed["revision"] = "new-rates"
    changed["profiles"][0]["price"] = {
        "input_per_million": "2",
        "output_per_million": "8",
        "cache_read_per_million": "0.5",
        "reasoning_uses_output_rate": True,
    }
    service.budgets = BudgetAuthority(BudgetInventory.model_validate(changed))
    checked = await PreflightService(service, principal).check(scope, money_suite.id)
    assert "budget_candidates_changed" in checked.errors
    assert "budget_configuration_proof_unavailable" in checked.errors
    new_suite, new_subject = await published_suite(configurations)
    checked = await PreflightService(service, principal).check(scope, new_suite.id)
    assert checked.price_coverage == "complete"
    assert new_subject.snapshot["budget"] != subject.snapshot["budget"]


async def test_runtime_and_metadata_candidate_catalog_use_same_stable_tie_order(configurations):
    service, _, scope, principal = configurations
    await native_inventory(configurations)
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO inference_models(id,endpoint_id,display_name,model_name,kind,settings) SELECT 'aaa-first',endpoint_id,'tie',model_name,kind,settings FROM inference_models WHERE id='e02-model'"
            )
        )
        await db.execute(text("UPDATE inference_models SET created_at='2026-01-01'"))
        await db.commit()
    async with service.uow_factory(service.auth(scope, principal)) as uow:
        runtime = await uow.inference_model.get_all(scope=scope)
        metadata = await uow.evaluation_configuration.candidate_metadata(scope)
        assert [item.id for item in runtime] == ["aaa-first", "e02-model", "e05-fallback"]
        assert [item["identity"]["model_id"] for item in metadata] == [item.id for item in runtime]
