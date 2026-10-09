"""Public metadata for native authoring; private contracts and credentials stay server-side."""

from typing import Literal
from uuid import UUID

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.evaluation.environment import EnvironmentVersion, ImageIdentity, VersionRef
from app.domain.evaluation.recording import RecordingJob


class InventoryTarget(VersionRef):
    kind: str


class InventoryCredential(VersionRef):
    target: VersionRef


class InventoryAdapter(ImmutableModel):
    name: str
    revision: str
    images: tuple[ImageIdentity, ...]
    fixtures: tuple[str, ...]
    healthchecks: tuple[str, ...]


class EnvironmentInventory(ImmutableModel):
    targets: tuple[InventoryTarget, ...]
    credentials: tuple[InventoryCredential, ...]
    adapters: tuple[InventoryAdapter, ...]


class EnvironmentChoice(ImmutableModel):
    version: EnvironmentVersion
    qualified: bool
    reason: str | None = None
    tool_names: tuple[str, ...] = ()


class EnvironmentPage(ImmutableModel):
    items: tuple[EnvironmentChoice, ...]
    next_cursor: str | None = None


class RecordingPage(ImmutableModel):
    items: tuple[RecordingJob, ...]
    next_cursor: str | None = None


class RecordingField(ImmutableModel):
    name: str
    type: Literal["string", "number", "integer", "boolean", "object", "array", "unknown"]
    required: bool = False
    nonsemantic: bool = False


class RecordingCandidate(ImmutableModel):
    tool: str
    step_id: str
    activity_id: UUID
    effect: str
    result_fields: tuple[RecordingField, ...]
    argument_fields: tuple[RecordingField, ...]
    requires_argument_replacement: bool


class RecordingCandidatePage(ImmutableModel):
    run_id: UUID
    at: str
    items: tuple[RecordingCandidate, ...]
    next_cursor: str | None = None


class BuiltinToolChoice(ImmutableModel):
    name: str
    mode: Literal["ask", "agent"]
