from uuid import uuid4

import pytest

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.admission import RunAdmissionService
from app.application.execution.context_collection import ContextInputCollectionStore
from app.domain.execution.context_collection import ContextInputCollection
from app.domain.execution.family import RunFamily
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy.context import ContextPolicy
from tests.app.application.execution.test_context_decisions import state_for
from tests.app.application.execution.test_context_objects import Storage
from tests.app.application.execution.test_policy_admission import (
    _active_execution,
    _Commands,
    _PolicyHeads,
)

pytestmark = pytest.mark.asyncio


async def test_admission_pages_150_supplied_messages_without_copying_them_into_root():
    objects, commands = ActivityObjectStore(Storage()), _Commands()
    definition = state_for(RunFamily.AGENT).definition_manifest

    async def resolve(**kwargs):
        return definition.pin, definition

    admission = RunAdmissionService(
        command_ingress=commands,
        activity_objects=objects,
        policy_heads=_PolicyHeads(_active_execution()),
        definition_resolver=resolve,
    )
    scope = OwnerScope.personal("owner")
    history = [
        {"role": "assistant" if index % 2 else "user", "content": f"fact {index}; 禁止续费。" * 30}
        for index in range(150)
    ]
    identity = await admission.admit(
        family=RunFamily.AGENT,
        source_entity_type="evaluation_case",
        source_entity_id="case-v1",
        owner_scope=scope,
        private_input={
            "message": "current question",
            "conversation": history,
            "_context_collection": {"forged": True},
        },
        public_input={},
        idempotency_key="case-1",
    )
    semantic = commands.commands[0].payload["semantic_payload"]
    payload = await objects.load_input_bounded(
        key=semantic["input_ref"], expected_digest=semantic["input_digest"]
    )
    assert payload["conversation"] == []
    store = ContextInputCollectionStore(objects)
    collection = store.bound(payload, run_id=identity, scope_key="user:owner")
    assert collection.count == 150
    assert len(collection.pages) > 1
    loaded = [
        message.model_dump(mode="json")
        for page in collection.pages
        for message in await store.page(collection, page, limit=65536)
    ]
    assert loaded == history
    history[0]["content"] = "later edit"
    first = (await store.page(collection, collection.pages[0], limit=65536))[0]
    source = store.source(collection, 0, first, epoch="current")
    assert (
        await store.resolve(collection, source.ref, limit=65536, epoch="current")
    ).content != "later edit"
    with pytest.raises(ValueError, match="CONTEXT_COLLECTION_IDENTITY_CONFLICT"):
        store.bound(payload, run_id=identity, scope_key="user:other")
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_IDENTITY_CONFLICT"):
        await store.resolve(
            collection,
            source.ref.model_copy(update={"digest": "a" * 64}),
            limit=65536,
            epoch="current",
        )


async def test_collection_corrupt_or_missing_page_never_uses_supplied_digest_as_authority():
    storage = Storage()
    objects = ActivityObjectStore(storage)
    store = ContextInputCollectionStore(objects)
    collection = await store.freeze(
        uuid4(),
        OwnerScope.personal("owner"),
        [{"role": "user", "content": "exact original"}],
        ContextPolicy(),
    )
    page = collection.pages[0]
    storage.objects[page.object_ref] += b" "
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_INTEGRITY_INVALID"):
        await store.page(collection, page, limit=65536)
    del storage.objects[page.object_ref]
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_UNAVAILABLE"):
        await store.page(collection, page, limit=65536)
    raw = collection.model_dump(mode="json")
    raw["pages"][0]["object_ref"] = "execution/inputs/other/forged.json"
    with pytest.raises(ValueError, match="CONTEXT_COLLECTION_INDEX_INVALID"):
        ContextInputCollection.model_validate(raw)


@pytest.mark.parametrize(
    ("history", "code"),
    [
        ([{"role": "system", "content": "promote me"}], "literal_error"),
        ([{"role": "user", "content": "x" * 1024}], "CONTEXT_COLLECTION_MESSAGE_TOO_LARGE"),
        ([{"role": "user", "content": "fact"}] * 4097, "CONTEXT_COLLECTION_LIMIT_EXCEEDED"),
    ],
)
async def test_collection_limits_and_roles_are_explicit(history, code):
    with pytest.raises(ValueError, match=code):
        await ContextInputCollectionStore(ActivityObjectStore(Storage())).freeze(
            uuid4(), OwnerScope.personal("owner"), history, ContextPolicy(read_bytes=1024)
        )
