"""Immutable context evidence. Trust is assigned by source adapters, not text."""

import hashlib
import json
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.domain.execution.definition import DefinitionPin, DefinitionValue, Digest
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.execution.tool_contract import RecordedContract
from app.domain.json_values import JsonValue, deep_freeze_json
from app.domain.runtime_policy.context import ContextPolicy

SourceKind = Literal[
    "platform", "user", "assistant", "tool", "knowledge", "memory", "attachment", "summary"
]


def context_digest(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def context_allowance(messages, candidates):
    from app.domain.execution.context_media import GPT41_IMAGE_TOKEN_BOUND

    count = sum(len(message.media_parts) for message in messages)
    if count and any(candidate.image_profile != "gpt41-inline-high-v1" for candidate in candidates):
        raise ValueError("CONTEXT_MEDIA_PROFILE_UNSUPPORTED")
    return (
        max(candidate.envelope_tokens + candidate.media_tokens for candidate in candidates)
        + count * GPT41_IMAGE_TOKEN_BOUND
    )


class ContextCandidateProfile(DefinitionValue):
    identity_digest: Digest
    profile_digest: Digest
    candidate_proof_digest: Digest
    input_cap: int = Field(ge=1)
    window: int = Field(ge=1)
    output_cap: int = Field(ge=1)
    # The conservative counter charges one token per UTF-8 byte, including
    # canonical JSON framing; an attested allowance covers provider framing.
    tokenizer: Literal["utf8-byte-upper-v1"] = "utf8-byte-upper-v1"
    envelope_tokens: int = Field(default=1024, ge=256, le=16384)
    media_tokens: int = Field(default=0, ge=0)
    image_profile: Literal["none", "gpt41-inline-high-v1"] = Field(
        default="none", exclude_if=lambda value: value == "none"
    )

    def input_budget(self, output: int) -> int:
        if not 0 < output <= self.output_cap:
            raise ValueError("CONTEXT_OUTPUT_PROFILE_UNSUPPORTED")
        budget = min(self.input_cap, self.window - output - max(1024, ceil(self.window * 0.05)))
        if budget <= 0:
            raise ValueError("CONTEXT_INPUT_PROFILE_UNSUPPORTED")
        return budget


class ContextBoundary(DefinitionValue):
    format_version: Literal[1] = 1
    scope_key: Annotated[str, Field(min_length=1, max_length=261)]
    collection_id: Annotated[str, Field(min_length=1, max_length=255)]
    through_position: int = Field(ge=0)
    current_input_digest: Digest
    # The requester belongs to the consuming Run, never a historical source Run.
    current_run_id: UUID | None = None
    checkpoint_revision: int | None = Field(default=None, ge=0)


class AcceptedContextCut(DefinitionValue):
    format_version: Literal[1] = 1
    scope_key: Annotated[str, Field(min_length=1, max_length=261)]
    collection_id: Annotated[str, Field(min_length=1, max_length=255)]
    through_position: int = Field(ge=0)
    checkpoint_revision: int = Field(default=0, ge=0)


class FormalContextSource(DefinitionValue):
    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    run_id: UUID
    formal_position: int = Field(ge=1)
    event_hash: Digest
    kind: Literal["user", "assistant"]
    object_ref: Annotated[str, Field(min_length=1, max_length=2048)]
    object_digest: Digest


class ContextSourceRef(DefinitionValue):
    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    scope_key: Annotated[str, Field(min_length=1, max_length=261)]
    run_id: UUID
    kind: SourceKind
    digest: Digest
    formal_position: int = Field(ge=0)
    authorization_epoch: Annotated[str, Field(min_length=1, max_length=128)]
    local_sequence: int = Field(default=0, ge=0)


def source_order(ref):
    return (bool(ref.local_sequence), ref.formal_position, ref.local_sequence, ref.source_id)


class ContextMessage(DefinitionValue):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    media_parts: tuple[dict[str, JsonValue], ...] = Field(
        default=(), max_length=10, exclude_if=lambda value: not value
    )
    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    tool_call_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    tool_calls: tuple[dict[str, JsonValue], ...] = Field(default=(), max_length=16)

    @field_validator("tool_calls")
    @classmethod
    def freeze_calls(cls, value):
        return tuple(deep_freeze_json(item) for item in value)

    @field_validator("media_parts")
    @classmethod
    def media(cls, value):
        from app.domain.execution.context_media import validate_inline_image

        for part in value:
            validate_inline_image(part)
        return tuple(deep_freeze_json(part) for part in value)

    @model_validator(mode="after")
    def protocol(self):
        if self.media_parts and (self.role != "user" or self.tool_calls or self.tool_call_id):
            raise ValueError("media requires a non-executable user message")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool message requires its exact call identity")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only assistant messages can contain tool calls")
        for call in self.tool_calls:
            if call.get("type") != "function" or not isinstance(call.get("id"), str):
                raise ValueError("unsupported tool call shape")
            function = call.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ValueError("tool function identity missing")  # noqa: TRY004 - Pydantic validation
            args = function.get("arguments")
            if not isinstance(args, str) or not isinstance(json.loads(args), dict):
                raise ValueError("tool arguments must be complete JSON object text")  # noqa: TRY004
        return self

    def wire(self):
        value = {"role": self.role, "content": self.content}
        if self.media_parts:
            value["content"] = [{"type": "text", "text": self.content}, *self.media_parts]
        if self.name is not None:
            value["name"] = self.name
        if self.tool_call_id is not None:
            value["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            value["tool_calls"] = list(self.tool_calls)
        return value


class ContextSource(DefinitionValue):
    ref: ContextSourceRef
    messages: tuple[ContextMessage, ...] = Field(min_length=1, max_length=33)
    protected: bool = False
    current_task: bool = False
    messages_digest: Digest | None = None
    original_closure: tuple[ContextSourceRef, ...] = Field(default=(), max_length=4096)

    @model_validator(mode="after")
    def source_protocol(self):
        if any(message.media_parts for message in self.messages) and (
            not self.protected or self.ref.kind != "attachment"
        ):
            raise ValueError("media must remain a protected attachment")
        if self.current_task and (not self.protected or self.ref.kind != "user"):
            raise ValueError("current task must be a protected user source")
        if context_digest([message.model_dump(mode="json") for message in self.messages]) != (
            self.messages_digest or self.ref.digest
        ):
            raise ValueError("source content digest mismatch")
        if any(item.scope_key != self.ref.scope_key for item in self.original_closure):
            raise ValueError("derived source crosses owner scope")
        if self.ref.kind == "platform":
            if not self.protected or any(message.role != "system" for message in self.messages):
                raise ValueError("platform source must be a protected system instruction")
        elif any(message.role == "system" for message in self.messages):
            raise ValueError("source cannot promote text to platform instructions")
        if self.ref.kind in {"knowledge", "memory", "attachment", "summary"} and any(
            message.role != "user" or message.tool_calls for message in self.messages
        ):
            raise ValueError("untrusted source must be a non-executable evidence message")
        if self.ref.kind == "summary" and not self.original_closure:
            raise ValueError("summary requires an original source closure")
        calls = [call["id"] for message in self.messages for call in message.tool_calls]
        results = [message.tool_call_id for message in self.messages if message.role == "tool"]
        if len(calls) != len(set(calls)) or sorted(calls) != sorted(results):
            raise ValueError("source must retain a complete tool call/result unit")
        outstanding = set()
        for message in self.messages:
            outstanding.update(call["id"] for call in message.tool_calls)
            if message.role == "tool":
                if message.tool_call_id not in outstanding:
                    raise ValueError("tool result precedes its call")
                outstanding.remove(message.tool_call_id)
        return self


class ContextOmission(DefinitionValue):
    source: ContextSourceRef
    reason: Literal["covered_by_summary", "unavailable", "not_selected"]


class ContextManifest(DefinitionValue):
    format_version: Literal[1] = 1
    definition_pin: DefinitionPin
    boundary: ContextBoundary
    policy: ContextPolicy
    candidates: tuple[ContextCandidateProfile, ...] = Field(min_length=1, max_length=16)
    tool_contract_digests: tuple[Digest, ...]
    tool_contracts: tuple[RecordedContract, ...] = Field(default=(), max_length=128)
    tools: tuple[dict[str, JsonValue], ...] = Field(max_length=128)
    sources: tuple[ContextSourceRef, ...] = Field(max_length=256)
    original_closure: tuple[ContextSourceRef, ...] = Field(max_length=4096)
    omissions: tuple[ContextOmission, ...] = ()
    messages: tuple[ContextMessage, ...]
    messages_digest: Digest
    estimated_tokens: int = Field(ge=0)
    serialized_bytes: int = Field(ge=0)
    input_budget: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    counting_method: Literal["utf8-byte-upper-v1"] = "utf8-byte-upper-v1"

    @field_validator("tools")
    @classmethod
    def freeze_tools(cls, value):
        return tuple(deep_freeze_json(item) for item in value)

    @model_validator(mode="after")
    def manifest_integrity(self):
        if self.messages_digest != context_digest([message.wire() for message in self.messages]):
            raise ValueError("context messages digest mismatch")
        if self.estimated_tokens > self.input_budget:
            raise ValueError("context exceeds fixed input budget")
        expected_budget = min(
            self.policy.max_input_tokens,
            *(candidate.input_budget(self.output_tokens) for candidate in self.candidates),
        )
        wire = canonical_json_bytes(
            {"messages": [message.wire() for message in self.messages], "tools": list(self.tools)}
        )
        estimated = len(wire) + context_allowance(self.messages, self.candidates)
        if (
            self.output_tokens != self.policy.main_output_tokens
            or self.input_budget != expected_budget
            or self.serialized_bytes != len(wire)
            or self.estimated_tokens != estimated
        ):
            raise ValueError("context counting evidence does not match its fixed profile")
        if len(self.tools) != len(self.tool_contract_digests):
            raise ValueError("context tool contract closure incomplete")
        if self.tool_contracts and (
            tuple(item.digest for item in self.tool_contracts) != self.tool_contract_digests
            or tuple(item.schema_body for item in self.tool_contracts) != self.tools
        ):
            raise ValueError("context tool contract content mismatch")
        if any(
            ref.scope_key != self.boundary.scope_key
            for ref in (*self.sources, *self.original_closure)
        ):
            raise ValueError("context crosses owner scope")
        return self

    @property
    def digest(self):
        return context_digest(self.model_dump(mode="json"))


class ContextCompactionGroup(DefinitionValue):
    """Compiler-owned message boundaries; never inferred from quoted source text."""

    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    kind: SourceKind
    start: int = Field(ge=1)
    message_count: int = Field(ge=2, le=34)
    label_sources: tuple[str, ...] = Field(min_length=1, max_length=4096)


class ContextCompactionChunk(DefinitionValue):
    ordinal: int = Field(ge=0, le=127)
    sources: tuple[ContextSourceRef, ...]
    messages: tuple[ContextMessage, ...]
    digest: Digest
    groups: tuple[ContextCompactionGroup, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def chunk_integrity(self):
        material = {
            "sources": [source.model_dump(mode="json") for source in self.sources],
            "messages": [message.wire() for message in self.messages],
        }
        if self.groups:
            material["groups"] = [group.model_dump(mode="json") for group in self.groups]
            position = 1
            refs = {ref.source_id: ref for ref in self.sources}
            for group in self.groups:
                if (
                    group.start != position
                    or position + group.message_count > len(self.messages)
                    or not set(group.label_sources) <= refs.keys()
                    or self.messages[position].wire()
                    != {
                        "role": "user",
                        "content": "Source identities for the following evidence: "
                        + ", ".join(group.label_sources),
                    }
                    or (
                        group.kind != "summary"
                        and (
                            group.source_id not in refs or refs[group.source_id].kind != group.kind
                        )
                    )
                ):
                    raise ValueError("compaction group evidence mismatch")
                position += group.message_count
            if position != len(self.messages):
                raise ValueError("compaction group evidence incomplete")
        if context_digest(material) != self.digest:
            raise ValueError("compaction chunk digest mismatch")
        return self


class ContextCompactionRequest(DefinitionValue):
    format_version: Literal[1] = 1
    definition_pin: DefinitionPin
    boundary: ContextBoundary
    chunks: tuple[ContextCompactionChunk, ...] = Field(min_length=1, max_length=128)
    candidates: tuple[ContextCandidateProfile, ...] = Field(min_length=1, max_length=16)
    policy: ContextPolicy
    input_budget: int = Field(ge=1)
    output_tokens: int = Field(ge=256, le=4096)

    @model_validator(mode="after")
    def request_integrity(self):
        budget = min(
            self.policy.max_input_tokens,
            *(candidate.input_budget(self.output_tokens) for candidate in self.candidates),
        )
        if self.output_tokens != self.policy.summary_output_tokens or self.input_budget != budget:
            raise ValueError("compaction counting profile mismatch")
        if len({chunk.ordinal for chunk in self.chunks}) != len(self.chunks):
            raise ValueError("duplicate compaction ordinal")
        for chunk in self.chunks:
            wire = canonical_json_bytes(
                {"messages": [message.wire() for message in chunk.messages], "tools": []}
            )
            estimate = len(wire) + context_allowance(chunk.messages, self.candidates)
            if estimate > budget or any(
                ref.scope_key != self.boundary.scope_key
                or ref.formal_position > self.boundary.through_position
                for ref in chunk.sources
            ):
                raise ValueError("compaction chunk exceeds its source or input boundary")
        return self

    @property
    def digest(self):
        return context_digest(self.model_dump(mode="json"))


class ContextCompilation(DefinitionValue):
    status: Literal["ready", "needs_compaction", "failed"]
    manifest: ContextManifest | None = None
    compaction: ContextCompactionRequest | None = None
    failure_code: str | None = None

    @model_validator(mode="after")
    def branch(self):
        expected = (
            self.status == "ready",
            self.status == "needs_compaction",
            self.status == "failed",
        )
        if (
            self.manifest is not None,
            self.compaction is not None,
            self.failure_code is not None,
        ) != expected:
            raise ValueError("context compilation must contain exactly its typed result")
        return self
