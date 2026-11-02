from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.execution.activities.context_prepare import ContextPrepareActivityHandler
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.context_compiler import compile_context
from app.application.execution.definition_registry import DefinitionRegistry
from app.application.ports.context_preparation import ContextCompilerInput
from app.domain.execution.context_models import ContextBoundary, ContextPolicy
from app.domain.execution.definition import DefinitionIntegrityError, HandlerImplementation
from app.domain.execution.events import _reject_public_secrets
from app.domain.models.scope import OwnerScope
from tests.app.application.execution.test_context_compiler import PROFILE, source
from tests.app.application.execution.test_context_objects import Storage
from tests.app.domain.execution.test_definition import manifest

pytestmark = pytest.mark.asyncio


async def setup(sources):
    policy = ContextPolicy()
    value = manifest(
        context_policy=policy,
        handlers=(
            HandlerImplementation(
                activity_type=ContextPrepareActivityHandler.activity_type, handler_version="1"
            ),
        ),
    )
    registry = DefinitionRegistry()
    registry.register(value, planner=lambda *args: None, compiler=compile_context)

    class PreparationStorage(Storage):
        async def get_bytes(self, key):
            return self.objects[key]

    storage = PreparationStorage()
    objects = ActivityObjectStore(storage)
    run_id = uuid4()
    key, digest = await objects.put_input(run_id, {"message": "current task"})
    inputs = ContextCompilerInput(
        pin=value.pin,
        boundary=ContextBoundary(
            scope_key="user:compiler-test",
            collection_id="activity-test",
            through_position=1000,
            current_input_digest=digest,
        ),
        policy=policy,
        candidates=(PROFILE,),
        summary_candidates=(PROFILE,),
        sources=tuple(sources),
    )

    class Preparation:
        async def materialize(self, request, context, payload, *, definition):
            assert definition == value
            assert payload["message"] == "current task"
            return inputs

    request = SimpleNamespace(
        input_ref=key,
        input_digest=digest,
        activity_id=uuid4(),
        definition_digest=value.digest,
        handler_version="1",
    )
    context = SimpleNamespace(
        run=SimpleNamespace(
            run_id=run_id,
            definition_pin=value.pin,
            definition_manifest=value,
            owner_scope=OwnerScope.personal("compiler-test"),
        )
    )
    handler = ContextPrepareActivityHandler(
        objects=objects, definitions=registry, preparation=Preparation()
    )
    return handler, request, context, objects, inputs


async def test_prepare_persists_identical_manifest_without_hidden_inference():
    handler, request, context, objects, _inputs = await setup(
        [source(1, "fact"), source(2, "task", protected=True)]
    )
    first = await handler.execute(request, context)
    second = await handler.execute(request, context)
    assert first.status == "succeeded"
    assert first.result_ref == second.result_ref
    assert first.decision_data == second.decision_data
    assert first.public_data["kind"] == "context"
    assert first.public_data["context"]["state"] == "ready"
    assert first.public_data["context"]["original_sources"] == 2
    _reject_public_secrets(first.public_data)
    stored = await objects.load_context_document(first.result_ref)
    assert stored["compilation"]["status"] == "ready"
    assert stored["compilation"]["manifest"][
        "definition_pin"
    ] == context.run.definition_pin.model_dump(mode="json")


async def test_prepare_emits_compaction_request_and_protected_overflow_is_typed():
    handler, request, context, objects, _inputs = await setup(
        [source(i, "x" * 300) for i in range(150)]
    )
    result = await handler.execute(request, context)
    assert result.status == "succeeded"
    assert result.decision_data["context_status"] == "needs_compaction"
    assert (await objects.load_context_document(result.result_ref))["compilation"]["compaction"][
        "chunks"
    ]
    handler, request, context, _objects, _inputs = await setup(
        [source(1, "x" * 40000, protected=True)]
    )
    result = await handler.execute(request, context)
    assert result.status == "failed"
    assert result.failure_code == "CONTEXT_REQUIRED_CONTENT_TOO_LARGE"


async def test_prepare_rejects_materialized_policy_or_boundary_drift():
    handler, request, context, _objects, inputs = await setup([source(1, "task", protected=True)])

    class Drift:
        async def materialize(self, *args, **kwargs):
            return inputs.model_copy(
                update={
                    "boundary": inputs.boundary.model_copy(
                        update={"current_input_digest": "f" * 64}
                    )
                }
            )

    handler.preparation = Drift()
    with pytest.raises(DefinitionIntegrityError, match="admitted Run input"):
        await handler.execute(request, context)


async def test_prepare_io_budget_failure_keeps_its_stable_code_and_public_stage():
    handler, request, context, _objects, _inputs = await setup([source(1, "fact")])

    class Exhausted:
        async def materialize(self, *args, **kwargs):
            raise ValueError("CONTEXT_PREPARE_READ_BUDGET_EXCEEDED")

    handler.preparation = Exhausted()
    result = await handler.execute(request, context)
    assert result.status == "failed"
    assert result.failure_code == "CONTEXT_PREPARE_READ_BUDGET_EXCEEDED"
    assert result.public_data == {"kind": "context", "context": {"stage": "prepare"}}
