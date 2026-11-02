"""Bounded private context materialization from accepted cuts and formal results."""

from types import SimpleNamespace

from sqlalchemy import select, text

from app.application.execution import activity_types
from app.application.execution.context_collection import ContextInputCollectionStore
from app.application.execution.context_evidence import protocol_sources
from app.application.execution.context_obligations import effect_obligations
from app.application.execution.context_system_prompt import context_system_prompt
from app.application.ports.context_preparation import ContextCompilerInput
from app.domain.evaluation.context_profile import context_profiles
from app.domain.execution.context_models import (
    AcceptedContextCut,
    ContextBoundary,
    ContextCompilation,
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)
from app.domain.execution.context_summary import ContextCompactBinding, ContextSummary
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.context_activity_sources import ContextActivitySources
from app.infrastructure.execution.context_checkpoints import publish_completed_checkpoints
from app.infrastructure.execution.context_formal_request import context_activity_request
from app.infrastructure.execution.context_original_lookup import OriginalLookup
from app.infrastructure.execution.context_original_reader import ContextOriginalReader
from app.infrastructure.execution.context_resource_authority import validate_input_resources
from app.infrastructure.execution.context_session_effects import session_effect_obligations
from app.infrastructure.execution.context_source_reader import ContextSourceReader
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.execution.postgres_context_checkpoint import PostgresContextCheckpointStore
from app.infrastructure.execution.postgres_context_sources import PostgresContextSources


class PostgresContextPreparation:
    def __init__(self, *, uow_factory, objects, storage, usage, tools, sources):
        self.uow_factory, self.objects, self.storage = uow_factory, objects, storage
        self.usage, self.tools, self.sources = usage, tools, sources

    async def materialize(self, request, context, payload, *, definition):
        policy, scope = definition.context_policy, context.run.owner_scope
        if payload.get("thinking_enabled"):
            raise ValueError("CONTEXT_RESOURCE_DEPENDENCIES_UNAVAILABLE")
        candidates = await self.usage.candidates(scope, context)
        summary_candidates, _, _ = await self.usage.summary_authority(scope, context)
        if candidates is None:
            raise ValueError("CONTEXT_CANDIDATE_PROFILE_UNAVAILABLE")
        allow_tools = request.input_payload.get("allow_tools") is True
        catalog = await self.tools.definitions(payload, context) if allow_tools else None
        definitions = catalog.definitions if catalog else ()
        if any(item.contract is None for item in definitions):
            raise ValueError("CONTEXT_TOOL_CONTRACT_INCOMPLETE")
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
            if context.run.source_entity_type == "session":
                cut = AcceptedContextCut.model_validate(payload.get("_context_boundary"))
            else:
                position = await work.db_session.scalar(
                    select(ExecutionEventORM.position).where(
                        ExecutionEventORM.owner_scope_key == key,
                        ExecutionEventORM.stream_type == "run",
                        ExecutionEventORM.stream_id == str(context.run.run_id),
                        ExecutionEventORM.stream_version == 1,
                    )
                )
                if position is None:
                    raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
                cut = AcceptedContextCut(
                    scope_key=key,
                    collection_id=str(context.run.run_id),
                    through_position=position - 1,
                )
            boundary = ContextBoundary(
                **cut.model_dump(exclude={"format_version"}),
                current_input_digest=request.input_digest,
                current_run_id=context.run.run_id,
            )
            _, epoch, state, actual_input = await self.sources.open(work, boundary)
            await validate_input_resources(work, scope, payload)
            if (
                actual_input != payload
                or state.definition_pin != context.run.definition_pin
                or state.retry_generation != request.generation
                or (request.activity_id, activity_types.CONTEXT_PREPARE, request.generation)
                not in state.requested_activities
            ):
                raise ValueError("CONTEXT_PREPARATION_REQUEST_MISMATCH")
            actual = await work.db_session.scalar(
                text(
                    "SELECT request_payload FROM execution_activity_tasks WHERE activity_id=:activity "
                    "AND aggregate_id=CAST(CAST(:run AS uuid) AS text) AND request_generation=:generation"
                ),
                {
                    "activity": request.activity_id,
                    "run": state.run_id,
                    "generation": request.generation,
                },
            )
            if canonical_json_bytes(actual) != canonical_json_bytes(request.input_payload):
                raise ValueError("CONTEXT_PREPARATION_REQUEST_MISMATCH")
            if state.source_entity_type == "evaluation_judge":
                from app.infrastructure.execution.context_judge_authority import (
                    judge_context_sources,
                )

                sources = await judge_context_sources(
                    work, state, scope=scope, scope_key=key, epoch=epoch, request=request
                )
                if (
                    len(
                        canonical_json_bytes([source.model_dump(mode="json") for source in sources])
                    )
                    > policy.prepare_bytes
                ):
                    raise ValueError("CONTEXT_SOURCE_BYTES_EXCEEDED")
                await work.commit()
                # Judge materials are protected in full: do not retrieve ambient
                # history, compact rubric anchors, or turn evidence into tools.
                return ContextCompilerInput(
                    pin=definition.pin,
                    boundary=boundary,
                    policy=policy,
                    candidates=context_profiles(
                        candidates, output_tokens=policy.main_output_tokens
                    ),
                    summary_candidates=context_profiles(
                        summary_candidates, output_tokens=policy.summary_output_tokens
                    ),
                    sources=sources,
                )
            collected, byte_count = [], 0

            def add(source):
                nonlocal byte_count
                byte_count += len(canonical_json_bytes(source.model_dump(mode="json")))
                if byte_count > policy.prepare_bytes or len(collected) >= 4096:
                    raise ValueError("CONTEXT_SOURCE_BYTES_EXCEEDED")
                collected.append(source)

            def fixed(identity, kind, content, *, current=False):
                message = ContextMessage(
                    role="system" if kind == "platform" else "user", content=content
                )
                digest = context_digest([message.model_dump(mode="json")])
                return ContextSource(
                    ref=ContextSourceRef(
                        source_id=f"{identity}:{state.run_id}",
                        scope_key=key,
                        run_id=state.run_id,
                        kind=kind,
                        digest=request.input_digest if current else digest,
                        formal_position=0,
                        authorization_epoch=epoch,
                    ),
                    messages=(message,),
                    messages_digest=digest,
                    protected=True,
                    current_task=current,
                )

            add(
                fixed(
                    "platform",
                    "platform",
                    context_system_prompt(payload, family=state.family.value),
                )
            )
            prompt = payload.get("message")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("CONTEXT_CURRENT_INPUT_INVALID")
            add(fixed("input", "user", prompt, current=True))
            from app.infrastructure.execution.context_file_authority import (
                attachment_source,
                validate_context_files,
            )

            files = await validate_context_files(work.db_session, scope, payload)
            text_attachments = set()
            for evidence in files:
                attachment = await attachment_source(
                    self.objects, evidence, run_id=state.run_id, scope_key=key, epoch=epoch
                )
                add(attachment)
                if evidence.representation == "text":
                    text_attachments.add(attachment.ref.source_id)
            obligations = effect_obligations(state, scope_key=key, epoch=epoch)
            if obligations is not None:
                add(obligations)
            for source in await session_effect_obligations(work, state, boundary, epoch=epoch):
                add(source)

            async def live(*_args):
                # sources.open holds the principal and membership locks until
                # this same transaction exits; no permission cache escapes it.
                return epoch

            await publish_completed_checkpoints(
                work,
                state,
                boundary,
                scope=scope,
                objects=self.objects,
                sources=self.sources,
            )
            checkpoint = await PostgresContextCheckpointStore(
                work.db_session,
                objects=self.objects,
                sources=self.sources,
            ).select(
                work,
                boundary,
                compiler_version=definition.compiler_version,
                authorization_epoch=epoch,
            )
            covered = set()
            if checkpoint is not None:
                for item in checkpoint.summaries:
                    closure = tuple(
                        ref.model_copy(update={"authorization_epoch": epoch})
                        for ref in item.original_closure
                    )
                    body = await self.objects.load_result_bounded(item.result_ref)
                    summary = ContextSummary.model_validate(body.get("summary"))
                    message = ContextMessage(role="user", content=summary.model_dump_json())
                    add(
                        ContextSource(
                            ref=ContextSourceRef(
                                source_id=f"checkpoint:{checkpoint.digest}:{item.activity_id}",
                                scope_key=key,
                                run_id=checkpoint.run_id,
                                kind="summary",
                                digest=item.result_digest,
                                formal_position=max(ref.formal_position for ref in closure),
                                authorization_epoch=epoch,
                            ),
                            messages=(message,),
                            messages_digest=context_digest([message.model_dump(mode="json")]),
                            original_closure=closure,
                        )
                    )
                    covered.update((ref.source_id, ref.digest) for ref in closure)
            original_lookups = ()
            lookup = (
                OriginalLookup(boundary, policy)
                if state.family.value == "ask" and policy.ask_retrievals
                else None
            )
            if payload.get("conversation"):
                raise ValueError("CONTEXT_COLLECTION_UNBOUND")
            if payload.get("_context_collection") is not None:
                if state.source_entity_type == "session":
                    raise ValueError("CONTEXT_SESSION_REQUIRES_FORMAL_CUT")
                store = ContextInputCollectionStore(self.objects)
                collection = store.bound(payload, run_id=state.run_id, scope_key=key)
                for page in collection.pages:
                    messages = await store.page(collection, page, limit=policy.read_bytes)
                    for ordinal, message in enumerate(messages, page.first):
                        source = store.source(collection, ordinal, message, epoch=epoch)
                        add(source)
                        if lookup is not None:
                            lookup.index.add(source.ref, message.content)
            if context.run.source_entity_type == "session":
                reader = ContextSourceReader(work.db_session, self.storage, authorizer=live)
                history = PostgresContextSources(
                    work.db_session, signing_secret=work.evaluation_budget.secret
                )
                cursor = None
                while True:
                    page = await history.page(cut, cursor=cursor, limit=100)
                    for descriptor in page.sources:
                        if lookup is not None:
                            await lookup.add(reader, cut, descriptor, epoch=epoch)
                        if (descriptor.source_id, descriptor.object_digest) in covered:
                            continue
                        source = await reader.read(cut, descriptor, limit=policy.read_bytes)
                        if descriptor.kind == "assistant":
                            previous = await self.sources.source_state(
                                work, scope, boundary, descriptor.run_id
                            )
                            closure = await self.sources.answer_dependencies(
                                work, previous, descriptor.object_ref, epoch=epoch
                            )
                            source = source.model_copy(update={"original_closure": closure})
                        add(source)
                    cursor = page.next_cursor
                    if cursor is None:
                        break
            activity_sources, evidence = ContextActivitySources(self.objects, self.storage), []
            evidence_bytes = 0
            kinds = {
                (identity, generation): kind
                for identity, kind, generation in state.requested_activities
            }
            for identity, generation, _, _, _ in state.activity_results:
                if generation != request.generation or kinds.get((identity, generation)) not in {
                    activity_types.MODEL_CALL,
                    activity_types.TOOL_CALL,
                    activity_types.RETRIEVAL_SEARCH,
                }:
                    continue
                evidence.append(
                    await activity_sources.read(
                        state,
                        identity,
                        generation,
                        scope_key=key,
                        epoch=epoch,
                        limit=policy.read_bytes,
                        before_read=live,
                    )
                )
                evidence_bytes += len(
                    canonical_json_bytes(
                        {
                            "ref": evidence[-1].ref.model_dump(mode="json"),
                            "message": evidence[-1].message.model_dump(mode="json"),
                        }
                    )
                )
                if len(evidence) > 4096 or evidence_bytes + byte_count > policy.prepare_bytes:
                    raise ValueError("CONTEXT_SOURCE_BYTES_EXCEEDED")
            for source in protocol_sources(evidence):
                add(source)
            for source in await self.completed_summaries(work, state, boundary, epoch):
                add(source)
            if lookup is not None:
                # Summary and answer closures retain original tool/KB results,
                # including sources no longer present as recent protocol units.
                reader = ContextOriginalReader(
                    uow_factory=self.uow_factory,
                    sources=self.sources,
                    objects=self.objects,
                    storage=self.storage,
                )
                indexed = {item[0] for item in lookup.index.documents}
                refs = {}
                dependencies = {
                    source.ref.source_id: source.original_closure for source in collected
                }
                attachment_runs = {state.run_id}
                for source in collected:
                    for ref in (source.ref, *source.original_closure):
                        if (
                            ref.source_id.startswith("attachment:")
                            and ref.run_id not in attachment_runs
                        ):
                            owner = await self.sources.source_state(
                                work, scope, boundary, ref.run_id
                            )
                            original = await self.objects.load_input_bounded(
                                key=owner.semantic_payload["input_ref"],
                                expected_digest=owner.semantic_payload["input_digest"],
                                limit=policy.read_bytes,
                            )
                            files = await validate_context_files(work.db_session, scope, original)
                            text_attachments.update(
                                f"attachment:{owner.run_id}:{item.file_id}"
                                for item in files
                                if item.representation == "text"
                            )
                            attachment_runs.add(ref.run_id)
                        if (
                            ref.source_id.startswith(("activity:", "collection:", "formal:"))
                            or ref.source_id in text_attachments
                        ) and refs.setdefault(ref.source_id, ref) != ref:
                            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
                missing = tuple(ref for ref in refs.values() if ref.source_id not in indexed)
                read_original = (
                    await reader.bind_in_uow(
                        work,
                        SimpleNamespace(boundary=boundary, policy=policy, original_closure=missing),
                    )
                    if missing
                    else None
                )
                for ref in missing:
                    if ref.source_id in indexed:
                        continue

                    async def read_span(offset, limit, *, selected=ref):
                        return await read_original(selected, offset=offset, limit=limit)

                    await lookup.add_original(ref, read_span)
                    indexed.add(ref.source_id)
                matches, record = lookup.search(prompt)
                original_lookups = (record,)
                for source in matches:
                    closure = dependencies.get(source.ref.source_id, ())
                    if (
                        source.ref.kind == "assistant"
                        and source.ref.source_id in lookup.descriptors
                    ):
                        descriptor = lookup.descriptors[source.ref.source_id]
                        previous = await self.sources.source_state(
                            work, scope, boundary, descriptor.run_id
                        )
                        closure = await self.sources.answer_dependencies(
                            work, previous, descriptor.object_ref, epoch=epoch
                        )
                    source = source.model_copy(update={"original_closure": closure})
                    old = next(
                        (item for item in collected if item.ref.source_id == source.ref.source_id),
                        None,
                    )
                    if old is not None:
                        # A lookup excerpt must not replace a complete tool protocol
                        # unit. Keep it as the source and protect the whole unit.
                        if any(message.tool_calls for message in old.messages):
                            source = old.model_copy(update={"protected": True})
                        collected.remove(old)
                        byte_count -= len(canonical_json_bytes(old.model_dump(mode="json")))
                    add(source)
            closure = {}
            for source in collected:
                for ref in (
                    source.original_closure
                    if source.ref.kind == "summary"
                    else (source.ref, *source.original_closure)
                ):
                    if closure.setdefault(ref.source_id, ref) != ref:
                        raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            await self.sources.validate_in_uow(work, boundary, tuple(closure.values()))
            await work.commit()
            return ContextCompilerInput(
                pin=definition.pin,
                boundary=boundary,
                policy=policy,
                candidates=context_profiles(candidates, output_tokens=policy.main_output_tokens),
                summary_candidates=context_profiles(
                    summary_candidates, output_tokens=policy.summary_output_tokens
                ),
                sources=tuple(collected),
                original_lookups=original_lookups,
                tools=tuple(item.tool_schema for item in definitions),
                tool_contract_digests=tuple(item.contract.digest for item in definitions),
                tool_contracts=tuple(item.contract for item in definitions),
                compactions_used=sum(
                    kind == activity_types.CONTEXT_COMPACT
                    for _, kind, _ in state.requested_activities
                ),
            )

    async def completed_summaries(self, work, state, boundary, epoch):
        values = []
        for identity, generation, result_ref, _, _ in state.activity_results:
            if (
                identity,
                activity_types.CONTEXT_COMPACT,
                generation,
            ) not in state.requested_activities:
                continue
            if (
                generation != state.retry_generation
                or (identity, "succeeded", generation) not in state.settled_activities
            ):
                continue
            payload = await context_activity_request(
                work.db_session, state, identity, generation, activity_types.CONTEXT_COMPACT
            )
            binding = ContextCompactBinding.model_validate(payload)
            if not any(
                item[0] == binding.prepare_activity_id
                and item[1] == generation
                and item[2] == binding.prepare_ref
                for item in state.activity_results
            ):
                raise ValueError("CONTEXT_SUMMARY_LINEAGE_UNAVAILABLE")
            prepared = await self.objects.load_context_document(binding.prepare_ref)
            plan = ContextCompilation.model_validate(prepared.get("compilation")).compaction
            if (
                plan is None
                or plan.digest != binding.compaction_digest
                or plan.boundary != boundary
                or plan.definition_pin != state.definition_pin
            ):
                raise ValueError("CONTEXT_SUMMARY_PREPARATION_MISMATCH")
            chunk = next(
                (
                    item
                    for item in plan.chunks
                    if item.ordinal == binding.ordinal and item.digest == binding.chunk_digest
                ),
                None,
            )
            if chunk is None:
                raise ValueError("CONTEXT_SUMMARY_PREPARATION_MISMATCH")
            body = await self.objects.load_result_bounded(result_ref)
            summary = ContextSummary.model_validate(body.get("summary"))
            if body.get("kind") != "context_compact":
                raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID")
            summary.require_chunk(chunk)
            message = ContextMessage(role="user", content=summary.model_dump_json())
            values.append(
                ContextSource(
                    ref=ContextSourceRef(
                        source_id=f"summary:{identity}:{generation}",
                        scope_key=boundary.scope_key,
                        run_id=state.run_id,
                        kind="summary",
                        digest=result_ref.rsplit("/", 1)[-1].removesuffix(".json"),
                        formal_position=max(item.formal_position for item in chunk.sources),
                        local_sequence=max(item.local_sequence for item in chunk.sources),
                        authorization_epoch=epoch,
                    ),
                    messages=(message,),
                    messages_digest=context_digest([message.model_dump(mode="json")]),
                    original_closure=chunk.sources,
                )
            )
        return tuple(values)
