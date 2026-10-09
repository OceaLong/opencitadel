"""Immutable recording metadata and exact, versioned matching; no approximate fallback."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import Field, field_validator, model_validator

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.json_values import deep_freeze_json
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.resource_pin import ResourceIdentity
from app.domain.models.tool_policy import ToolExecutionPolicy

MAX_RECORDING_BYTES = 20 * 1024 * 1024
MAX_RECORDING_SLOTS = 10_000
MAX_RECORDING_TOTAL_BYTES = 200 * 1024 * 1024


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def recording_key(
    tool: str,
    contract: str,
    args: dict,
    branch: str,
    ordinal: int,
    *,
    parallel_group: str = "",
    rule_version: int = 1,
) -> str:
    if type(ordinal) is not int or ordinal < 0 or type(rule_version) is not int or rule_version < 1:
        raise ValueError("invalid_recording_identity")
    if not tool or not contract or not branch or not isinstance(args, dict):
        raise ValueError("invalid_recording_identity")
    return hashlib.sha256(
        canonical([rule_version, tool, contract, args, branch, parallel_group, ordinal])
    ).hexdigest()


def validate_typed(value, schema):
    # Remote refs would turn validation into an external operation. Require a self-contained schema.
    def refs(node):
        if isinstance(node, dict):
            for reference in ("$ref", "$dynamicRef", "$recursiveRef"):
                if reference in node and not str(node[reference]).startswith("#"):
                    raise ValueError("external_schema_reference")
            for item in node.values():
                refs(item)
        elif isinstance(node, list):
            for item in node:
                refs(item)

    refs(schema)
    canonical(value)
    Draft202012Validator.check_schema(schema)
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("recording_type_invalid")


class MatchRule(ImmutableModel):
    version: Literal[1] = 1
    excluded_fields: tuple[str, ...] = ()

    def normalize(self, arguments: dict, schema: dict) -> dict:
        validate_typed(arguments, schema)
        properties = schema.get("properties", {})
        if len(set(self.excluded_fields)) != len(self.excluded_fields) or any(
            properties.get(name, {}).get("x-recording-nonsemantic") is not True
            for name in self.excluded_fields
        ):
            raise ValueError("semantic_argument_exclusion")
        return {
            key: deepcopy(value)
            for key, value in arguments.items()
            if key not in self.excluded_fields
        }


def sanitize_result(
    result: dict, schema: dict, allowed_fields: tuple[str, ...], replacements: dict
) -> dict:
    properties = schema.get("properties", {})
    if (
        not isinstance(result, dict)
        or not set(allowed_fields) <= properties.keys()
        or not replacements.keys() <= set(allowed_fields)
    ):
        raise ValueError("invalid_recording_fields")
    value = {key: deepcopy(item) for key, item in result.items() if key in allowed_fields}
    value.update(deepcopy(replacements))
    validate_typed(value, schema)
    if len(canonical(value)) > MAX_RECORDING_BYTES:
        raise ValueError("recording_result_too_large")
    return value


class RecordedContract(ImmutableModel):
    name: str = Field(min_length=1)
    pack: str = Field(min_length=1)
    schema_body: dict
    policy: ToolExecutionPolicy
    connector_id: str | None = None
    source_name: str | None = None
    connector_bindings: dict[str, str] = Field(default_factory=dict)
    binding_revision: str = Field(min_length=1)
    authority_revision: str = Field(min_length=1)

    @field_validator("schema_body", "connector_bindings", mode="after")
    @classmethod
    def immutable_metadata(cls, value):
        return deep_freeze_json(value)

    @property
    def digest(self):
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()

    @property
    def arguments_schema(self):
        return self.schema_body["function"]["parameters"]


class RecordingSelection(ImmutableModel):
    tool: str = Field(min_length=1)
    allowed_fields: tuple[str, ...]
    replacements: dict = Field(default_factory=dict)
    argument_replacements: dict = Field(default_factory=dict)
    rule: MatchRule = Field(default_factory=MatchRule)


class RecordingJob(ImmutableModel):
    id: UUID
    source_run_id: UUID
    revision: int = 1
    status: Literal["queued", "running", "ready", "failed"] = "queued"
    result_version: UUID | None = None
    error: str | None = None
    created_at: datetime | None = None


class RecordingCitationEvidence(ImmutableModel):
    source_at: str = Field(min_length=1)
    output_content_id: UUID
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    citations: tuple[KnowledgeCitation, ...] = Field(max_length=1000)


class RecordingSlot(ImmutableModel):
    id: UUID
    tool: str
    contract_digest: str
    match_key: str
    rule: MatchRule
    branch: str
    parallel_group: str = ""
    ordinal: int = Field(ge=0)
    source_step_id: str | None = None
    source_parent_step_id: str | None = None
    object_id: UUID
    result_digest: str
    result_bytes: int = Field(gt=0, le=MAX_RECORDING_BYTES)
    simulated_effect: bool
    citation_evidence: RecordingCitationEvidence | None = None


class RecordingManifest(ImmutableModel):
    id: UUID
    job_id: UUID
    source_run_id: UUID
    revision: int = 1
    schema_version: Literal[1] = 1
    catalog_fingerprint: str
    contracts: tuple[RecordedContract, ...]
    slots: tuple[RecordingSlot, ...]
    pins: tuple[ResourceIdentity, ...]

    @model_validator(mode="after")
    def unique_slots(self):
        if (
            len(self.slots) > MAX_RECORDING_SLOTS
            or len({s.match_key for s in self.slots}) != len(self.slots)
            or len({c.name for c in self.contracts}) != len(self.contracts)
        ):
            raise ValueError("ambiguous_recording_manifest")
        contracts = {c.digest for c in self.contracts}
        if any(s.contract_digest not in contracts for s in self.slots):
            raise ValueError("recording_contract_missing")
        return self


class RecordedToolResult(ImmutableModel):
    result_ref: str
    simulated_effect: bool
    recording_revision: int


class ReplayCoverage(ImmutableModel):
    total: int
    consumed: int
    unused: int
    mismatches: int
    # Coverage is execution validity evidence, never a quality score.
    quality_eligible: bool
