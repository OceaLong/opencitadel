from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.utils.time_utils import utc_now

PLATFORM_EMBEDDING_DIMENSIONS = 1536
_DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class InferenceProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    AZURE = "azure"


class InferenceModelKind(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"


class InferencePurpose(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class InferenceProbeStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class ResourceVisibility(StrEnum):
    GLOBAL = "global"
    PRIVATE = "private"


class InferenceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vision: bool = False
    vision_with_tools: bool = True
    audio: bool = False
    video: bool = False
    image_generation: bool = False
    max_image_bytes: int = Field(default=_DEFAULT_MAX_IMAGE_BYTES, ge=1)
    max_images_per_request: int = Field(default=8, ge=1)
    max_video_frames: int = Field(default=8, ge=1)
    image_encoding: Literal["data_url", "url"] = "data_url"
    structured_output: Literal["auto", "json_schema", "json_object", "none"] = "auto"


class ChatModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[InferenceModelKind.CHAT] = InferenceModelKind.CHAT
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_output_tokens: int = Field(default=8192, ge=1)


class EmbeddingModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[InferenceModelKind.EMBEDDING] = InferenceModelKind.EMBEDDING
    dimensions: Literal[1536] = PLATFORM_EMBEDDING_DIMENSIONS
    max_batch_size: int = Field(default=64, ge=1, le=2048)


InferenceModelSettings = Annotated[
    ChatModelSettings | EmbeddingModelSettings,
    Field(discriminator="kind"),
]


class InferenceEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    display_name: str = ""
    provider: InferenceProvider = InferenceProvider.OPENAI
    base_url: str = "https://api.openai.com/v1"
    credential: str = ""
    credential_configured: bool = False
    owner_user_id: str | None = None
    team_id: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class InferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    endpoint_id: str = ""
    display_name: str = ""
    model_name: str = ""
    kind: InferenceModelKind = InferenceModelKind.CHAT
    settings: InferenceModelSettings = Field(default_factory=ChatModelSettings)
    input_price_per_million: float = Field(default=0.0, ge=0)
    output_price_per_million: float = Field(default=0.0, ge=0)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    capabilities: InferenceCapabilities = Field(default_factory=InferenceCapabilities)
    owner_user_id: str | None = None
    team_id: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_settings_kind(self) -> InferenceModel:
        if self.settings.kind != self.kind:
            raise ValueError("inference model kind must match settings kind")
        if (
            self.kind == InferenceModelKind.EMBEDDING
            and self.capabilities != InferenceCapabilities()
        ):
            raise ValueError("embedding models cannot declare chat capabilities")
        return self


class InferenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    purpose: InferencePurpose
    model_id: str
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ResolvedInferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: InferenceModel
    endpoint: InferenceEndpoint
    binding: InferenceBinding | None = None

    @property
    def id(self) -> str:
        return self.model.id

    @property
    def model_name(self) -> str:
        return self.model.model_name

    @property
    def display_name(self) -> str:
        return self.model.display_name

    @property
    def provider(self) -> InferenceProvider:
        return self.endpoint.provider

    @property
    def base_url(self) -> str:
        return self.endpoint.base_url

    @property
    def credential(self) -> str:
        return self.endpoint.credential

    @property
    def extra_params(self) -> dict[str, Any]:
        return self.model.extra_params

    @property
    def capabilities(self) -> InferenceCapabilities:
        return self.model.capabilities

    @property
    def temperature(self) -> float:
        if not isinstance(self.model.settings, ChatModelSettings):
            raise TypeError("embedding models do not have chat temperature")
        return self.model.settings.temperature

    @property
    def max_output_tokens(self) -> int:
        if not isinstance(self.model.settings, ChatModelSettings):
            raise TypeError("embedding models do not have max output tokens")
        return self.model.settings.max_output_tokens

    @property
    def max_tokens(self) -> int:
        return self.max_output_tokens


class InferenceProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: InferenceProbeStatus
    message: str
    error_key: str | None = None


def purpose_accepts_kind(purpose: InferencePurpose, kind: InferenceModelKind) -> bool:
    expected = {
        InferencePurpose.CHAT: InferenceModelKind.CHAT,
        InferencePurpose.EMBEDDING: InferenceModelKind.EMBEDDING,
        InferencePurpose.RERANK: InferenceModelKind.CHAT,
    }
    return expected[purpose] == kind
