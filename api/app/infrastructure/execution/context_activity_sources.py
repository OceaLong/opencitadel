"""Source adapters consume formal completion references, never a tool's claimed role."""

import json
from uuid import UUID

from app.application.execution import activity_types
from app.application.execution.context_evidence import ActivityContextEvidence
from app.application.execution.context_objects import ContextObjectStore
from app.domain.execution.context_index import OriginalContentIndex
from app.domain.execution.context_models import ContextMessage, ContextSourceRef


class ContextActivitySources:
    def __init__(self, objects, storage):
        self.objects, self.storage = objects, storage

    async def read(self, state, identity, generation, *, scope_key, epoch, limit, before_read):
        kind = next(
            (
                kind
                for activity, kind, gen in state.requested_activities
                if activity == identity and gen == generation
            ),
            None,
        )
        result = next(
            (
                ref
                for activity, gen, ref, _, _ in state.activity_results
                if activity == identity and gen == generation
            ),
            None,
        )
        if (identity, "succeeded", generation) not in state.settled_activities or result is None:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
        if kind not in {
            activity_types.MODEL_CALL,
            activity_types.TOOL_CALL,
            activity_types.RETRIEVAL_SEARCH,
        }:
            raise ValueError("CONTEXT_SOURCE_KIND_UNSUPPORTED")
        if not result.startswith(f"execution/results/{identity}/"):
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        await before_read()
        body = await self.objects.load_result_bounded(result, limit=limit)
        if "_context_index" in body:
            index = OriginalContentIndex.model_validate(body["_context_index"])
            if (
                index.scope_key != scope_key
                or index.run_id != state.run_id
                or index.activity_id != identity
            ):
                raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")

            async def current(_index):
                await before_read()

            data, following = await ContextObjectStore(self.storage).read_span(
                index, limit=min(limit, 8192), before_read=current
            )
            content = data.decode()
            if following is not None:
                if index.message_metadata.get("role") == "tool":
                    content = json.dumps(
                        {
                            "_context_excerpt": {
                                "source_id": f"activity:{identity}:{generation}",
                                "text": content,
                                "offset": 0,
                                "next_offset": following,
                                "total_bytes": index.content_bytes,
                            }
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                else:
                    content += f"\n[Excerpt; read_source activity:{identity}:{generation} for remaining original bytes.]"
            message = {**index.message_metadata, "content": content}
        else:
            message = body.get("message")
        if not isinstance(message, dict):
            raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID")  # noqa: TRY004 - stable failure code
        expected_role = {
            activity_types.MODEL_CALL: "assistant",
            activity_types.TOOL_CALL: "tool",
            activity_types.RETRIEVAL_SEARCH: "user",
        }[kind]
        if kind == activity_types.RETRIEVAL_SEARCH:
            # Legacy retrieval producers emitted system; the adapter assigns
            # evidence trust and never copies an authority role from source text.
            message = {"role": "user", "content": message.get("content")}
        elif message.get("role") != expected_role:
            raise ValueError("CONTEXT_SOURCE_PROTOCOL_INVALID")
        await before_read()
        return ActivityContextEvidence(
            ref=ContextSourceRef(
                source_id=f"activity:{identity}:{generation}",
                scope_key=scope_key,
                run_id=state.run_id,
                kind="knowledge" if kind == activity_types.RETRIEVAL_SEARCH else expected_role,
                digest=result.rsplit("/", 1)[-1].removesuffix(".json"),
                formal_position=0,
                authorization_epoch=epoch,
                local_sequence=next(
                    index + 1
                    for index, item in enumerate(state.activity_results)
                    if item[0] == identity and item[1] == generation
                ),
            ),
            message=ContextMessage.model_validate(message),
        )

    @staticmethod
    def identity(source):
        try:
            prefix, identity, generation = source.source_id.split(":")
            if prefix != "activity" or str(int(generation)) != generation or int(generation) < 0:
                raise ValueError
            return UUID(identity), int(generation)
        except (ValueError, AttributeError) as error:
            raise ValueError("CONTEXT_SOURCE_HANDLE_INVALID") from error
