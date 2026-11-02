"""Build the Ask index from formal original pages, including checkpoint-covered sources."""

import json

from app.application.execution.context_lookup import ContextLexicalIndex
from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)


class OriginalLookup:
    def __init__(self, boundary, policy):
        self.index = ContextLexicalIndex(boundary, byte_limit=policy.prepare_bytes)
        self.policy = policy
        self.descriptors = {}

    async def add(self, reader, cut, descriptor, *, epoch):
        offset, parts = 0, []
        while True:
            value, following, total = await reader.read_span(
                cut, descriptor, offset=offset, limit=min(self.policy.read_bytes, 65536)
            )
            if self.index.byte_count + total > self.policy.prepare_bytes:
                raise ValueError("CONTEXT_LOOKUP_BYTES_EXCEEDED")
            parts.append(value)
            if following is None:
                break
            if following <= offset:
                raise ValueError("CONTEXT_LOOKUP_SPAN_INVALID")
            offset = following
        ref = ContextSourceRef(
            source_id=descriptor.source_id,
            scope_key=cut.scope_key,
            run_id=descriptor.run_id,
            kind=descriptor.kind,
            digest=descriptor.object_digest,
            formal_position=descriptor.formal_position,
            authorization_epoch=epoch,
        )
        self.index.add(ref, "".join(parts))
        self.descriptors[ref.source_id] = descriptor

    async def add_original(self, ref, read_span):
        offset, parts, expected_total = 0, [], None
        while True:
            value, following, total = await read_span(offset, min(self.policy.read_bytes, 65536))
            if self.index.byte_count + total > self.policy.prepare_bytes:
                raise ValueError("CONTEXT_LOOKUP_BYTES_EXCEEDED")
            if expected_total is None:
                expected_total = total
            end = offset + len(value.encode())
            if (
                total != expected_total
                or end > total
                or (following is None and end != total)
                or (following is not None and (following != end or following <= offset))
            ):
                raise ValueError("CONTEXT_LOOKUP_SPAN_INVALID")
            parts.append(value)
            if following is None:
                break
            offset = following
        content = "".join(parts)
        self.index.add(
            ref,
            content,
            searchable_ranges=retrieval_ranges(content) if ref.kind == "knowledge" else None,
        )

    def search(self, query):
        hits, record = self.index.search(query, limit=self.policy.ask_retrievals)
        values = []
        for ref, offset, content in hits:
            message = ContextMessage(
                role=ref.kind if ref.kind in {"user", "assistant"} else "user",
                content=(
                    f"[Original source {ref.source_id}; bytes {offset}..{offset + len(content.encode())}]\n"
                    + content
                ),
            )
            values.append(
                ContextSource(
                    ref=ref,
                    messages=(message,),
                    messages_digest=context_digest([message.model_dump(mode="json")]),
                    protected=True,
                )
            )
        return values, record


def retrieval_ranges(content):
    """Index evidence values, excluding the echoed query; retain original byte offsets."""
    decoder = json.JSONDecoder()
    position = 0

    def whitespace(index):
        while index < len(content) and content[index].isspace():
            index += 1
        return index

    try:
        position = whitespace(position)
        if content[position] != "{":
            raise ValueError
        position += 1
        ranges, keys = [], set()
        while True:
            position = whitespace(position)
            if content[position] == "}":
                position += 1
                break
            key, position = decoder.raw_decode(content, position)
            if not isinstance(key, str) or key in keys:
                raise ValueError
            keys.add(key)
            position = whitespace(position)
            if content[position] != ":":
                raise ValueError
            start = whitespace(position + 1)
            value, position = decoder.raw_decode(content, start)
            if key == "sources":
                if not isinstance(value, list):
                    raise ValueError
                ranges.append((len(content[:start].encode()), len(content[:position].encode())))
            position = whitespace(position)
            if content[position] == "}":
                position += 1
                break
            if content[position] != ",":
                raise ValueError
            position += 1
        if whitespace(position) != len(content) or "sources" not in keys:
            raise ValueError
        return tuple(ranges)
    except (ValueError, IndexError, RecursionError) as error:
        raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID") from error
