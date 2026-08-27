"""OpenAI Images and Gemini Imagen transport implementation."""

from __future__ import annotations

import base64
import logging

import httpx

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.external.file_storage import FileStorage
from app.domain.models.inference import (
    InferenceProvider,
    ResolvedInferenceModel,
)
from app.domain.services.vision_service import upload_image_bytes_to_storage
from app.infrastructure.security.outbound_http import (
    DEFAULT_OUTBOUND_NETWORK_POLICY,
    create_ssrf_safe_async_client,
)

logger = logging.getLogger(__name__)
_MAX_PROVIDER_RESPONSE_BYTES = 25 * 1024 * 1024


class ProviderImageGenerator:
    def __init__(
        self,
        *,
        outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
    ) -> None:
        self._outbound_policy = outbound_policy

    async def generate(
        self,
        prompt: str,
        model: ResolvedInferenceModel,
        file_storage: FileStorage,
        *,
        size: str = "1024x1024",
        quality: str = "standard",
        owner_user_id: str | None = None,
        team_id: str | None = None,
    ) -> str | None:
        return await generate_image(
            prompt,
            model,
            file_storage,
            size=size,
            quality=quality,
            owner_user_id=owner_user_id,
            team_id=team_id,
            outbound_policy=self._outbound_policy,
        )


def _ensure_bounded_provider_response(response: httpx.Response) -> None:
    declared = response.headers.get("content-length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            declared_size = 0
        if declared_size > _MAX_PROVIDER_RESPONSE_BYTES:
            raise ValueError("图像提供商响应超过允许大小")
    if len(response.content) > _MAX_PROVIDER_RESPONSE_BYTES:
        raise ValueError("图像提供商响应超过允许大小")


async def generate_image(
    prompt: str,
    model: ResolvedInferenceModel,
    file_storage: FileStorage,
    *,
    size: str = "1024x1024",
    quality: str = "standard",
    owner_user_id: str | None = None,
    team_id: str | None = None,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
) -> str | None:
    if model.provider in (InferenceProvider.OPENAI, InferenceProvider.AZURE):
        return await _generate_openai_image(
            prompt,
            model,
            file_storage,
            size=size,
            quality=quality,
            owner_user_id=owner_user_id,
            team_id=team_id,
            outbound_policy=outbound_policy,
        )
    if model.provider == InferenceProvider.GEMINI:
        return await _generate_gemini_image(
            prompt,
            model,
            file_storage,
            owner_user_id=owner_user_id,
            team_id=team_id,
            outbound_policy=outbound_policy,
        )
    logger.warning("Provider %s 不支持图像生成", model.provider)
    return None


async def _generate_openai_image(
    prompt: str,
    model: ResolvedInferenceModel,
    file_storage: FileStorage,
    *,
    size: str,
    quality: str,
    owner_user_id: str | None,
    team_id: str | None,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
) -> str | None:
    base_url = str(model.base_url).rstrip("/")
    url = (
        f"{base_url}/images/generations"
        if base_url.endswith("/v1")
        else f"{base_url}/v1/images/generations"
    )
    headers = {
        "Authorization": f"Bearer {model.credential}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model.extra_params.get("image_model", "dall-e-3"),
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "response_format": "b64_json",
    }
    try:
        async with create_ssrf_safe_async_client(
            timeout=120.0,
            follow_redirects=False,
            outbound_policy=outbound_policy,
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            _ensure_bounded_provider_response(response)
            encoded = response.json()["data"][0].get("b64_json", "")
            if not encoded:
                return None
            return await upload_image_bytes_to_storage(
                file_storage,
                base64.b64decode(encoded),
                "image/png",
                owner_user_id=owner_user_id,
                team_id=team_id,
                fallback_to_proxy=True,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("OpenAI 图像生成失败: %s", exc)
        return None


async def _generate_gemini_image(
    prompt: str,
    model: ResolvedInferenceModel,
    file_storage: FileStorage,
    *,
    owner_user_id: str | None,
    team_id: str | None,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
) -> str | None:
    base_url = str(model.base_url).rstrip("/")
    model_name = model.extra_params.get(
        "image_model",
        "imagen-3.0-generate-002",
    )
    url = f"{base_url}/models/{model_name}:predict"
    headers = {
        "x-goog-api-key": model.credential,
        "Content-Type": "application/json",
    }
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1},
    }
    try:
        async with create_ssrf_safe_async_client(
            timeout=120.0,
            follow_redirects=False,
            outbound_policy=outbound_policy,
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            _ensure_bounded_provider_response(response)
            predictions = response.json().get("predictions") or []
            if not predictions:
                return None
            encoded = predictions[0].get("bytesBase64Encoded", "")
            if not encoded:
                return None
            return await upload_image_bytes_to_storage(
                file_storage,
                base64.b64decode(encoded),
                "image/png",
                owner_user_id=owner_user_id,
                team_id=team_id,
                fallback_to_proxy=True,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Gemini 图像生成失败: %s", exc)
        return None


__all__ = ["ProviderImageGenerator", "generate_image"]
