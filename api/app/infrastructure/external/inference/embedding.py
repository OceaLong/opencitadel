from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from openai import AsyncOpenAI, OpenAIError

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.errors import BadRequestError, ServerRequestsError
from app.domain.models.inference import (
    EmbeddingModelSettings,
    InferenceModelKind,
    ResolvedInferenceModel,
)
from app.infrastructure.security.outbound_http import (
    DEFAULT_OUTBOUND_NETWORK_POLICY,
    create_ssrf_safe_async_client,
)


class EmbeddingAdapter(Protocol):
    async def embed_batch(self, contents: Sequence[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedding:
    def __init__(
        self,
        resolved: ResolvedInferenceModel,
        *,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        if resolved.model.kind != InferenceModelKind.EMBEDDING or not isinstance(
            resolved.model.settings,
            EmbeddingModelSettings,
        ):
            raise BadRequestError(
                "Embedding 适配器只能使用 Embedding 模型",
                error_key="inference.errors.bindingKindMismatch",
            )
        timeout = float(resolved.extra_params.get("request_timeout", 60))
        self._client = AsyncOpenAI(
            base_url=resolved.base_url,
            api_key=resolved.credential,
            max_retries=0,
            http_client=create_ssrf_safe_async_client(
                timeout=timeout,
                outbound_policy=outbound_policy,
            ),
        )
        self._model_name = resolved.model_name
        self._timeout = timeout

    async def embed_batch(self, contents: Sequence[str]) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(
                model=self._model_name,
                input=list(contents),
                timeout=self._timeout,
            )
        except (OpenAIError, OSError, TimeoutError) as exc:
            raise ServerRequestsError(
                f"Embedding 调用失败: {exc}",
                error_key="inference.errors.embeddingRequestFailed",
            ) from exc
        ordered = sorted(response.data, key=lambda item: item.index)
        return [[float(value) for value in item.embedding] for item in ordered]
