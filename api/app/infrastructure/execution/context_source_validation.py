"""Formal source integrity and current requester checks share the permit UoW."""

import json

from sqlalchemy import select

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.context_collection import ContextInputCollectionStore
from app.application.execution.context_obligations import effect_obligations
from app.application.execution.context_system_prompt import context_system_prompt
from app.domain.execution.context_index import OriginalContentIndex
from app.domain.execution.context_models import (
    AcceptedContextCut,
    ContextCompilation,
    ContextMessage,
    context_digest,
)
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.context_activity_sources import ContextActivitySources
from app.infrastructure.execution.context_authority import ContextRunAuthority
from app.infrastructure.execution.context_formal_request import context_activity_request
from app.infrastructure.execution.context_resource_authority import (
    validate_input_resources,
    validate_resource_evidence,
)
from app.infrastructure.execution.context_source_descriptors import ContextSourceDescriptors
from app.infrastructure.execution.context_source_reader import ContextSourceReader
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


class PostgresContextSourceValidator:
    def __init__(self, *, uow_factory, storage):
        self.uow_factory, self.storage = uow_factory, storage
        self.objects = ActivityObjectStore(storage)

    async def validate(self, boundary, sources):
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            await self.validate_in_uow(work, boundary, sources)

    async def open(self, work, boundary):
        authority = ContextRunAuthority(
            work.db_session, signing_secret=work.evaluation_budget.secret
        )
        scope, epoch = await authority.lock(boundary)
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                boundary.current_run_id,
                owner_scope=(None if scope.team_id else scope.user_id, scope.team_id),
            )
        ).replay.state
        if state.definition_manifest is None or state.definition_manifest.context_policy is None:
            raise ValueError("CONTEXT_DEFINITION_UNAVAILABLE")
        semantic = state.semantic_payload
        if semantic.get("input_digest") != boundary.current_input_digest:
            raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        payload = await self.objects.load_input_bounded(
            key=semantic["input_ref"],
            expected_digest=boundary.current_input_digest,
            limit=state.definition_manifest.context_policy.read_bytes,
        )
        from app.infrastructure.execution.context_skill_authority import validate_context_skill

        await validate_context_skill(work.db_session, scope, payload)
        from app.infrastructure.execution.context_file_authority import validate_context_files

        await validate_context_files(work.db_session, scope, payload)
        if state.source_entity_type == "session":
            cut = AcceptedContextCut.model_validate(payload.get("_context_boundary"))
            if (cut.scope_key, cut.collection_id, cut.through_position) != (
                boundary.scope_key,
                boundary.collection_id,
                boundary.through_position,
            ) or cut.collection_id != state.source_entity_id:
                raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
            if boundary.checkpoint_revision != cut.checkpoint_revision:
                raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        else:
            position = await work.db_session.scalar(
                select(ExecutionEventORM.position).where(
                    ExecutionEventORM.owner_scope_key == boundary.scope_key,
                    ExecutionEventORM.stream_type == "run",
                    ExecutionEventORM.stream_id == str(state.run_id),
                    ExecutionEventORM.stream_version == 1,
                )
            )
            if (
                position is None
                or boundary.collection_id != str(state.run_id)
                or boundary.through_position != position - 1
            ):
                raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        return scope, epoch, state, payload

    async def source_state(self, work, scope, boundary, identity, *, require_completed=True):
        state = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                identity, owner_scope=(None if scope.team_id else scope.user_id, scope.team_id)
            )
        ).replay.state
        if identity != boundary.current_run_id:
            completed = await work.db_session.scalar(
                select(ExecutionEventORM.event_id).where(
                    ExecutionEventORM.owner_scope_key == boundary.scope_key,
                    ExecutionEventORM.stream_type == "run",
                    ExecutionEventORM.stream_id == str(identity),
                    ExecutionEventORM.event_type
                    == ("RunCompleted" if require_completed else "RunCreated"),
                    ExecutionEventORM.position <= boundary.through_position,
                )
            )
            if (
                completed is None
                or state.source_entity_type != "session"
                or state.source_entity_id != boundary.collection_id
            ):
                raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        return state

    async def answer_dependencies(self, work, state, result_ref, *, epoch):
        result = next((item for item in state.activity_results if item[2] == result_ref), None)
        if (
            result is None
            or (result[0], activity_types.MODEL_CALL, result[1]) not in state.requested_activities
        ):
            raise ValueError("CONTEXT_DERIVED_SOURCE_UNAVAILABLE")
        request = await context_activity_request(
            work.db_session, state, result[0], result[1], activity_types.MODEL_CALL
        )
        ref = request.get("context_ref") if isinstance(request, dict) else None
        if not isinstance(ref, str) or not any(
            item[2] == ref
            and item[1] == result[1]
            and (item[0], activity_types.CONTEXT_PREPARE, item[1]) in state.requested_activities
            and (item[0], "succeeded", item[1]) in state.settled_activities
            for item in state.activity_results
        ):
            raise ValueError("CONTEXT_DERIVED_SOURCE_UNAVAILABLE")
        body = await self.objects.load_context_document(ref)
        manifest = ContextCompilation.model_validate(body.get("compilation")).manifest
        if (
            body.get("kind") != "context_prepare"
            or manifest is None
            or manifest.definition_pin != state.definition_pin
            or manifest.digest != request.get("context_digest")
            or manifest.boundary.current_input_digest != state.semantic_payload.get("input_digest")
        ):
            raise ValueError("CONTEXT_DERIVED_SOURCE_UNAVAILABLE")
        return tuple(
            ref.model_copy(update={"authorization_epoch": epoch})
            for ref in manifest.original_closure
        )

    async def validate_in_uow(self, work, boundary, sources):
        scope, epoch, state, payload = await self.open(work, boundary)
        policy = state.definition_manifest.context_policy
        by_id = {source.source_id: source for source in sources}
        if len(sources) > 4096 or len(by_id) != len(sources):
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")

        async def live(*_args):
            # open() keeps user/member FOR SHARE locks until this caller-owned
            # transaction ends. Re-querying the same locked principal for every
            # chunk adds no authority and makes long closure validation quadratic.
            return epoch

        descriptors = ContextSourceDescriptors(work.db_session)
        reader = ContextSourceReader(work.db_session, self.storage, authorizer=live)
        activities = ContextActivitySources(self.objects, self.storage)
        collections = ContextInputCollectionStore(self.objects)
        cut = AcceptedContextCut(
            scope_key=boundary.scope_key,
            collection_id=boundary.collection_id,
            through_position=boundary.through_position,
        )
        states, inputs = {state.run_id: state}, {state.run_id: payload}
        completed_runs = {state.run_id}
        for source in sources:
            if (
                source.scope_key != boundary.scope_key
                or source.formal_position > boundary.through_position
            ):
                raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
            if source.authorization_epoch != epoch:
                raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")
            # Every non-user source must prove its producing Run and all inputs;
            # current ownership of a conversation alone cannot authorize a derived answer.
            requires_completion = not (
                source.kind == "user" and source.source_id.startswith("formal:")
            ) and not source.source_id.startswith("effects:")
            if source.run_id not in states or (
                requires_completion and source.run_id not in completed_runs
            ):
                states[source.run_id] = await self.source_state(
                    work,
                    scope,
                    boundary,
                    source.run_id,
                    require_completed=requires_completion,
                )
                if requires_completion:
                    completed_runs.add(source.run_id)
            owner = states[source.run_id]
            if source.source_id.startswith(("judge:", "judge-platform:")):
                from app.infrastructure.execution.context_judge_authority import (
                    judge_context_sources,
                )

                actual = await judge_context_sources(
                    work, owner, scope=scope, scope_key=boundary.scope_key, epoch=epoch
                )
                if source not in tuple(item.ref for item in actual):
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            elif source.source_id.startswith(("input:", "platform:")):
                if source.run_id not in inputs:
                    inputs[source.run_id] = await self.objects.load_input_bounded(
                        key=owner.semantic_payload["input_ref"],
                        expected_digest=owner.semantic_payload["input_digest"],
                        limit=policy.read_bytes,
                    )
                original = inputs[source.run_id]
                # Resource adapters must attest their complete immutable closure.
                # Until their typed evidence is available, never treat text as proof.
                await validate_input_resources(work, scope, original)
                if source.source_id == f"input:{source.run_id}":
                    valid = (
                        source.kind == "user"
                        and source.digest == owner.semantic_payload["input_digest"]
                    )
                elif source.source_id == f"platform:{source.run_id}":
                    message = ContextMessage(
                        role="system",
                        content=context_system_prompt(original, family=owner.family.value),
                    )
                    valid = source.kind == "platform" and source.digest == context_digest(
                        [message.model_dump(mode="json")]
                    )
                else:
                    valid = False
                if not valid or source.formal_position != 0:
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            elif source.source_id.startswith("attachment:"):
                from app.infrastructure.execution.context_file_authority import (
                    attachment_source,
                    validate_context_files,
                )

                if source.run_id not in inputs:
                    inputs[source.run_id] = await self.objects.load_input_bounded(
                        key=owner.semantic_payload["input_ref"],
                        expected_digest=owner.semantic_payload["input_digest"],
                        limit=policy.read_bytes,
                    )
                files = await validate_context_files(work.db_session, scope, inputs[source.run_id])
                matches = [
                    item
                    for item in files
                    if source.source_id == f"attachment:{source.run_id}:{item.file_id}"
                ]
                if len(matches) != 1:
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
                actual = await attachment_source(
                    self.objects,
                    matches[0],
                    run_id=source.run_id,
                    scope_key=boundary.scope_key,
                    epoch=epoch,
                )
                if actual.ref != source:
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            elif source.source_id.startswith("effects:"):
                try:
                    _prefix, identity, version = source.source_id.split(":")
                    if (
                        identity != str(source.run_id)
                        or str(int(version)) != version
                        or int(version) <= 0
                    ):
                        raise ValueError
                except (ValueError, TypeError) as error:
                    raise ValueError("CONTEXT_SOURCE_HANDLE_INVALID") from error
                if source.run_id != boundary.current_run_id:
                    position = await work.db_session.scalar(
                        select(ExecutionEventORM.position).where(
                            ExecutionEventORM.owner_scope_key == boundary.scope_key,
                            ExecutionEventORM.stream_type == "run",
                            ExecutionEventORM.stream_id == str(source.run_id),
                            ExecutionEventORM.stream_version == int(version),
                        )
                    )
                    if position is None or position > boundary.through_position:
                        raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
                fixed = (
                    await PostgresVerifiedRunLoader(work.db_session).load(
                        source.run_id,
                        owner_scope=(None if scope.team_id else scope.user_id, scope.team_id),
                        at_version=int(version),
                    )
                ).replay.state
                obligations = effect_obligations(fixed, scope_key=boundary.scope_key, epoch=epoch)
                if obligations is None or obligations.ref != source:
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            elif source.source_id.startswith("collection:"):
                if source.run_id not in inputs:
                    inputs[source.run_id] = await self.objects.load_input_bounded(
                        key=owner.semantic_payload["input_ref"],
                        expected_digest=owner.semantic_payload["input_digest"],
                        limit=policy.read_bytes,
                    )
                collection = collections.bound(
                    inputs[source.run_id], run_id=owner.run_id, scope_key=boundary.scope_key
                )
                await collections.resolve(collection, source, limit=policy.read_bytes, epoch=epoch)
            elif source.source_id.startswith("formal:"):
                descriptor = await descriptors.resolve(boundary, source)
                value = await reader.read(cut, descriptor, limit=policy.read_bytes)
                if value.ref != source:
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
                if source.kind == "assistant":
                    for dependency in await self.answer_dependencies(
                        work, owner, descriptor.object_ref, epoch=epoch
                    ):
                        if by_id.get(dependency.source_id) != dependency:
                            raise ValueError("CONTEXT_DERIVED_SOURCE_UNAVAILABLE")
            elif source.source_id.startswith("activity:"):
                identity, generation = activities.identity(source)
                value = await activities.read(
                    owner,
                    identity,
                    generation,
                    scope_key=boundary.scope_key,
                    epoch=epoch,
                    limit=policy.read_bytes,
                    before_read=live,
                )
                if value.ref != source:
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
                if source.kind in {"knowledge", "tool"}:
                    # Empty retrieval is still a real formal result, but has no
                    # resource authority to carry forward. Nonempty evidence
                    # requires the resource-specific closure below this boundary.
                    result_ref = next(
                        ref
                        for activity, gen, ref, _, _ in owner.activity_results
                        if activity == identity and gen == generation
                    )
                    body = await self.objects.load_result_bounded(
                        result_ref, limit=policy.read_bytes
                    )
                    evidence = (
                        OriginalContentIndex.model_validate(
                            body["_context_index"]
                        ).resource_evidence
                        if "_context_index" in body
                        else body.get("resource_evidence")
                    )
                    if source.run_id not in inputs:
                        inputs[source.run_id] = await self.objects.load_input_bounded(
                            key=owner.semantic_payload["input_ref"],
                            expected_digest=owner.semantic_payload["input_digest"],
                            limit=policy.read_bytes,
                        )
                    if (
                        source.kind == "tool"
                        and evidence is None
                        and inputs[source.run_id].get("resource_bindings")
                    ):
                        raise ValueError("CONTEXT_RESOURCE_DEPENDENCIES_UNAVAILABLE")
                    if evidence is None and source.kind == "knowledge":
                        try:
                            retrieval = json.loads(value.message.content)
                        except (ValueError, TypeError) as error:
                            raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID") from error
                        if not isinstance(retrieval, dict) or retrieval.get("sources") != []:
                            raise ValueError("CONTEXT_RESOURCE_DEPENDENCIES_UNAVAILABLE")
                    elif evidence is not None:
                        await validate_resource_evidence(
                            work, scope, inputs[source.run_id], evidence
                        )
            else:
                raise ValueError("CONTEXT_SOURCE_KIND_UNSUPPORTED")
        await live()
