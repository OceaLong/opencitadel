"""Non-secret immutable identity of a vector coordinate space."""

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import ConflictError
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    EmbeddingModelSettings,
    ResolvedInferenceModel,
)


class EmbeddingSpaceMismatch(ConflictError):
    def __init__(self, message: str = "EMBEDDING_SPACE_MISMATCH: rebuild the vector index"):
        super().__init__(message, error_key="inference.errors.embeddingSpaceMismatch")


class EmbeddingRevisionUnverified(ConflictError):
    def __init__(self):
        super().__init__(
            "EMBEDDING_REVISION_UNVERIFIED: provider does not prove an immutable revision",
            error_key="inference.errors.embeddingRevisionUnverified",
        )


class EmbeddingSpaceDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: Literal[1] = 1
    provider: str
    configured_model: str
    model_id: str
    endpoint_id: str
    endpoint_origin_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: Literal[1536] = PLATFORM_EMBEDDING_DIMENSIONS
    semantic_parameters_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_preprocessing: Literal["identity-v1"] = "identity-v1"
    query_preprocessing: Literal["identity-v1"] = "identity-v1"
    normalization: Literal["provider-output-v1"] = "provider-output-v1"
    # No current adapter verifies an immutable provider revision. A configured
    # alias, even a date-shaped one, is not evidence of immutable weights.
    provider_revision: None = None
    revision_assurance: Literal["unverified_alias"] = "unverified_alias"

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.model_dump(mode="json")).encode()).hexdigest()

    def require_verified(self) -> None:
        raise EmbeddingRevisionUnverified()

    @classmethod
    def from_resolved(cls, resolved: ResolvedInferenceModel) -> "EmbeddingSpaceDescriptor":
        if not isinstance(resolved.model.settings, EmbeddingModelSettings):
            raise EmbeddingSpaceMismatch("embedding space requires an embedding model")
        if resolved.model.endpoint_id != resolved.endpoint.id:
            raise EmbeddingSpaceMismatch("model and resolved endpoint identities do not match")
        # Transport controls do not change vector semantics. Prices and secrets
        # are separate model/endpoint fields and are never included here.
        semantics = {
            key: value
            for key, value in resolved.extra_params.items()
            if key
            not in {
                "request_timeout",
                "max_retries",
                "max_batch_size",
                "concurrency",
                "credential",
                "api_key",
                "input_price_per_million",
                "output_price_per_million",
            }
        }
        return cls(
            provider=resolved.provider.value,
            configured_model=resolved.model_name,
            model_id=resolved.id,
            endpoint_id=resolved.endpoint.id,
            endpoint_origin_digest=hashlib.sha256(
                resolved.base_url.rstrip("/").encode()
            ).hexdigest(),
            dimensions=resolved.model.settings.dimensions,
            semantic_parameters_digest=hashlib.sha256(_canonical(semantics).encode()).hexdigest(),
        )


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def validate_embedding(vector: object, dimensions: int = PLATFORM_EMBEDDING_DIMENSIONS) -> None:
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError(f"embedding dimensions must be {dimensions}")
    if not all(type(value) in (int, float) and math.isfinite(value) for value in vector):
        raise ValueError("embedding contains non-finite or non-numeric values")
