"""Shared, versioned tool contracts and exact invocation approval subjects.

The recording contract remains the sole normalized tool descriptor. Version 1
retains its original wire shape and digest; version 2 adds execution semantics.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_serializer, model_validator

from app.domain.evaluation.configuration import digest
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.execution.effect import EffectCapability
from app.domain.json_values import deep_freeze_json
from app.domain.models.tool_policy import ToolEffect, ToolExecutionPolicy, ToolIdempotency


class EffectContract(ImmutableModel):
    capability: EffectCapability = EffectCapability.MANUAL_ONLY
    execute: bool = True
    resolve: bool = False
    cancel: bool = False
    key_scope: str | None = None
    retention_seconds: int | None = Field(default=None, gt=0)
    conflict_rule: Literal["reject_changed_intent"] = "reject_changed_intent"
    not_found_means_not_applied: bool = False
    target_rule: Literal["all_arguments_v1"] = "all_arguments_v1"

    @model_validator(mode="after")
    def _coherent_capability(self):
        if self.capability in {"dedupe_resolve", "dedupe_only"} and (
            not self.key_scope or self.retention_seconds is None
        ):
            raise ValueError("durable key capability requires its scope and retention")
        if self.capability in {"dedupe_resolve", "resolve_only"} and not self.resolve:
            raise ValueError("resolve capability requires a resolver")
        return self


TOOL_RESULT_SCHEMA = {
    "type": "object",
    "required": ["success"],
    "properties": {
        "success": {"type": "boolean"},
        "data": {},
        "message": {"type": ["string", "null"]},
    },
    "additionalProperties": True,
}


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
    contract_schema_version: Literal[1, 2] = 1
    implementation_revision: str | None = None
    output_schema: dict | None = None
    effect_contract: EffectContract | None = None

    @field_validator("schema_body", "connector_bindings", "output_schema", mode="after")
    @classmethod
    def immutable_metadata(cls, value):
        return deep_freeze_json(value) if value is not None else None

    @model_validator(mode="after")
    def _versioned_semantics(self):
        extra = (self.implementation_revision, self.output_schema, self.effect_contract)
        if self.contract_schema_version == 2 and any(value is None for value in extra):
            raise ValueError("version 2 tool contract requires execution semantics")
        if self.contract_schema_version == 1 and any(value is not None for value in extra):
            raise ValueError("execution semantics require tool contract version 2")
        if (
            self.effect_contract is not None
            and self.effect_contract.capability == "native_safe"
            and self.pack in {"mcp", "a2a"}
        ):
            raise ValueError("remote contract cannot claim native recovery safety")
        return self

    @model_serializer(mode="wrap")
    def _versioned_wire(self, handler):
        value = handler(self)
        if self.contract_schema_version == 1:
            for key in (
                "contract_schema_version",
                "implementation_revision",
                "output_schema",
                "effect_contract",
            ):
                value.pop(key, None)
        return value

    @property
    def digest(self):
        return digest(self.model_dump(mode="json"))

    @property
    def arguments_schema(self):
        return self.schema_body["function"]["parameters"]

    @property
    def recovery_safe(self):
        return (
            self.contract_schema_version == 2 and self.effect_contract.capability == "native_safe"
        )

    @property
    def requires_operation(self):
        # Remote read-only declarations are not a recovery capability. Journal
        # every invocation that cannot safely be repeated so an interrupted RPC
        # always has a scoped outcome record, even when it is not a business write.
        return not self.recovery_safe

    @property
    def is_pure_clarification(self):
        return (
            self.pack not in {"mcp", "a2a"}
            and self.recovery_safe
            and self.policy.effect == ToolEffect.INTERACTIVE
            and self.policy.approval_kind == "clarification"
        )


def execution_contract(contract: RecordedContract) -> RecordedContract:
    """Trusted local defaults. Remote annotations never promote recovery safety."""
    if contract.contract_schema_version == 2:
        return contract
    native = contract.pack not in {"mcp", "a2a"}
    safe = native and contract.policy.idempotency == ToolIdempotency.SAFE
    return RecordedContract.model_validate(
        {
            **contract.model_dump(mode="json"),
            "contract_schema_version": 2,
            "implementation_revision": f"{contract.pack}-tool-contract-v1"
            if native
            else "connector-adapter-v1",
            "output_schema": TOOL_RESULT_SCHEMA,
            "effect_contract": EffectContract(
                capability="native_safe" if safe else "manual_only"
            ).model_dump(mode="json"),
        }
    )


class ToolApprovalSubject(ImmutableModel):
    schema_version: Literal[1] = 1
    invocation_id: UUID
    originating_activity_id: UUID
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    contract_ref: str = Field(min_length=1)
    contract_object_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_epoch: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def digest(self):
        return digest(self.model_dump(mode="json"))


class ToolContractChanged(ValueError):
    """No physical send occurred; the exact intent needs renewed authorization."""

    def __init__(self, reason="contract_changed"):
        self.reason = reason
        super().__init__(reason)


class ToolPreparationFailed(RuntimeError):
    """Infrastructure failed before a physical tool invocation was permitted."""
