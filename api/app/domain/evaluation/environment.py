"""Registered test authorities and fenced environment lifecycle; no runtime clients."""

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from app.domain.evaluation.configuration import digest
from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.recording import RecordedContract

Positive = Annotated[StrictInt, Field(gt=0)]
State = Literal[
    "allocated", "preparing", "ready", "leased", "cleaning", "verified_clean", "quarantine"
]
Phase = Literal["prepare", "reset", "verify_ready", "cleanup", "verify_clean"]


class ImageIdentity(ImmutableModel):
    kind: Literal["registry_digest", "local_content_id"]
    value: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9./:_-]+$")

    @model_validator(mode="after")
    def registry(self):
        if self.kind == "registry_digest" and not self.repository:
            raise ValueError("image_repository_required")
        if self.kind == "local_content_id" and self.repository is not None:
            raise ValueError("local_image_repository_forbidden")
        return self

    @property
    def reference(self):
        return f"{self.repository}@{self.value}" if self.repository else self.value


class VersionRef(ImmutableModel):
    id: UUID
    revision: Positive


class TestTarget(ImmutableModel):
    id: UUID
    revision: Positive = 1
    physical_resource: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9:._/-]+$")
    kind: Literal["http", "mcp", "a2a", "actuator"]
    endpoint: str = Field(max_length=2048)
    protocol: Literal["http-fixture-v1", "mcp-stateless-json-2025-03-26", "a2a-jsonrpc-0.3"] = (
        "http-fixture-v1"
    )
    connector_id: str | None = None
    connector_revision: str | None = None
    shared: bool = False
    reset_adapter: str | None = None
    # Full contracts are registered by an administrator, never configuration authors.
    contracts: tuple[RecordedContract, ...] = ()
    allowed_agent_ids: tuple[str, ...] = ()
    enabled: bool = True

    @field_validator("endpoint")
    @classmethod
    def endpoint_only(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("test_endpoint_invalid")
        return value

    @model_validator(mode="after")
    def controlled(self):
        if self.shared and not self.reset_adapter:
            raise ValueError("shared_target_reset_verify_required")
        if self.kind in {"mcp", "a2a", "actuator"} and (
            not self.connector_id or not self.connector_revision
        ):
            raise ValueError("test_connector_binding_required")
        expected_protocol = {"mcp": "mcp-stateless-json-2025-03-26", "a2a": "a2a-jsonrpc-0.3"}.get(
            self.kind
        )
        if expected_protocol and self.protocol != expected_protocol:
            raise ValueError("test_target_protocol_required")
        for contract in self.contracts:
            if (
                contract.connector_id != self.connector_id
                or contract.binding_revision != self.connector_revision
            ):
                raise ValueError("test_contract_binding_mismatch")
        return self


class TestCredentialRef(ImmutableModel):
    id: UUID
    revision: Positive = 1
    target: VersionRef
    # Locator is opaque, only registered resolver code may interpret it.
    locator: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9:._/-]+$")
    resolver: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class EnvironmentLimits(ImmutableModel):
    concurrency: Positive = 2
    memory_mb: Annotated[StrictInt, Field(ge=128, le=8192)] = 1024
    cpu_millis: Annotated[StrictInt, Field(ge=100, le=8000)] = 1000
    pids: Annotated[StrictInt, Field(ge=16, le=1024)] = 256
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=86400)] = 1800


class EnvironmentVersion(ImmutableModel):
    id: UUID
    revision: Positive = 1
    image_digest: ImageIdentity
    fixture_revision: str = Field(min_length=1, max_length=128)
    allowed_targets: tuple[VersionRef, ...] = ()
    credential_refs: tuple[VersionRef, ...] = ()
    limits: EnvironmentLimits = EnvironmentLimits()
    reset_adapter: str = Field(min_length=1, max_length=64)
    adapter_revision: str = Field(min_length=1, max_length=128)
    healthcheck_revision: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def distinct(self):
        for refs in (self.allowed_targets, self.credential_refs):
            if len(refs) > 100 or len({ref.id for ref in refs}) != len(refs):
                raise ValueError("environment_duplicate_refs")
        return self


class CaseSlot(ImmutableModel):
    workspace: str = Field(min_length=1, max_length=261)
    batch_id: UUID
    case_id: UUID
    config_version: UUID
    repeat: Annotated[StrictInt, Field(ge=1, le=5)]


class EnvironmentLease(ImmutableModel):
    id: UUID
    environment_version: UUID
    case_slot: CaseSlot
    generation: Positive
    revision: Positive
    state: State
    requester: dict = Field(default_factory=dict)
    repair_authorized: bool = False
    expires_at: datetime | None = None
    resources: tuple[dict, ...] = ()
    actual_versions: dict = Field(default_factory=dict)

    @property
    def namespace(self):
        return (
            "e04-"
            + digest(
                {
                    "slot": self.case_slot.model_dump(mode="json"),
                    "lease": str(self.id),
                    "generation": self.generation,
                }
            )[:40]
        )


class EnvironmentOperation(ImmutableModel):
    id: UUID
    lease_id: UUID
    generation: Positive
    lease_revision: Positive
    phase: Phase
    claim_generation: Annotated[StrictInt, Field(ge=0)] = 0


def reusable(state: str) -> bool:
    return state == "verified_clean"


def transition(lease: EnvironmentLease, state: State, *, repair=False, administrator=False):
    if lease.state == "verified_clean":
        raise ValueError("invalid_environment_transition")
    if lease.state == "quarantine":
        if repair and not administrator:
            raise PermissionError("environment_repair_admin_required")
        if state != "cleaning" or not repair or not administrator:
            raise ValueError("invalid_environment_transition")
    elif (
        state != "quarantine"
        and state
        not in {
            "allocated": {"preparing", "cleaning"},
            "preparing": {"ready", "cleaning"},
            "ready": {"leased", "cleaning"},
            "leased": {"cleaning"},
            "cleaning": {"verified_clean"},
            "verified_clean": set(),
        }[lease.state]
    ):
        raise ValueError("invalid_environment_transition")
    return lease.model_copy(
        update={
            "state": state,
            "revision": lease.revision + 1,
            "repair_authorized": bool(repair and administrator) or lease.repair_authorized,
        }
    )
