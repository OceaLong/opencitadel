"""Explicit local vector-space fixtures; no Provider or database calls."""

from app.domain.models.embedding_space import EmbeddingSpaceDescriptor
from app.domain.models.inference import (
    EmbeddingModelSettings,
    InferenceEndpoint,
    InferenceModel,
    InferenceModelKind,
    ResolvedInferenceModel,
)


def resolved_embedding(model_id="model-1", model_name="embedding-a"):
    return ResolvedInferenceModel(
        endpoint=InferenceEndpoint(
            id="endpoint-1", base_url="https://example.com/v1", credential="secret"
        ),
        model=InferenceModel(
            id=model_id,
            endpoint_id="endpoint-1",
            model_name=model_name,
            kind=InferenceModelKind.EMBEDDING,
            settings=EmbeddingModelSettings(),
        ),
    )


def embedding_space():
    return EmbeddingSpaceDescriptor.from_resolved(resolved_embedding())
