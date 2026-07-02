#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""图像生成与编辑服务（OpenAI Images / Gemini Imagen）。"""
import base64
import io
import logging
from typing import Optional, Tuple

import httpx

from app.domain.external.file_storage import FileStorage
from app.domain.models.llm_model import LLMModel, LLMProvider
from app.domain.services.vision_service import upload_image_bytes_to_storage

logger = logging.getLogger(__name__)


async def generate_image(
        prompt: str,
        model: LLMModel,
        file_storage: FileStorage,
        *,
        size: str = "1024x1024",
        quality: str = "standard",
        owner_user_id: Optional[str] = None,
        team_id: Optional[str] = None,
) -> Optional[str]:
    """生成图像并上传对象存储，返回预签名 URL 或代理 URL。"""
    if model.provider in (LLMProvider.OPENAI, LLMProvider.AZURE):
        return await _generate_openai_image(
            prompt, model, file_storage, size=size, quality=quality,
            owner_user_id=owner_user_id, team_id=team_id,
        )
    if model.provider == LLMProvider.GEMINI:
        return await _generate_gemini_image(
            prompt, model, file_storage,
            owner_user_id=owner_user_id, team_id=team_id,
        )
    logger.warning("Provider %s 不支持图像生成", model.provider)
    return None


async def _generate_openai_image(
        prompt: str,
        model: LLMModel,
        file_storage: FileStorage,
        *,
        size: str,
        quality: str,
        owner_user_id: Optional[str],
        team_id: Optional[str],
) -> Optional[str]:
    base_url = str(model.base_url).rstrip("/")
    url = f"{base_url}/images/generations" if base_url.endswith("/v1") else f"{base_url}/v1/images/generations"
    headers = {"Authorization": f"Bearer {model.api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model.extra_params.get("image_model", "dall-e-3"),
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "response_format": "b64_json",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            b64 = data["data"][0].get("b64_json", "")
            if not b64:
                return None
            image_bytes = base64.b64decode(b64)
            return await upload_image_bytes_to_storage(
                file_storage, image_bytes, "image/png",
                owner_user_id=owner_user_id,
                team_id=team_id,
                fallback_to_proxy=True,
            )
    except Exception as exc:
        logger.error("OpenAI 图像生成失败: %s", exc)
        return None


async def _generate_gemini_image(
        prompt: str,
        model: LLMModel,
        file_storage: FileStorage,
        *,
        owner_user_id: Optional[str],
        team_id: Optional[str],
) -> Optional[str]:
    base_url = str(model.base_url).rstrip("/")
    model_name = model.extra_params.get("image_model", "imagen-3.0-generate-002")
    url = f"{base_url}/models/{model_name}:predict"
    headers = {"x-goog-api-key": model.api_key, "Content-Type": "application/json"}
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            predictions = data.get("predictions") or []
            if not predictions:
                return None
            b64 = predictions[0].get("bytesBase64Encoded", "")
            if not b64:
                return None
            image_bytes = base64.b64decode(b64)
            return await upload_image_bytes_to_storage(
                file_storage, image_bytes, "image/png",
                owner_user_id=owner_user_id,
                team_id=team_id,
                fallback_to_proxy=True,
            )
    except Exception as exc:
        logger.error("Gemini 图像生成失败: %s", exc)
        return None


async def edit_image(
        image_bytes: bytes,
        mask_bytes: bytes,
        prompt: str,
        model: LLMModel,
        file_storage: FileStorage,
        *,
        mime_type: str = "image/png",
        owner_user_id: Optional[str] = None,
        team_id: Optional[str] = None,
) -> Optional[Tuple[bytes, str]]:
    """图像编辑/inpaint，返回 (image_bytes, mime_type)。"""
    if model.provider in (LLMProvider.OPENAI, LLMProvider.AZURE):
        return await _edit_openai_image(
            image_bytes, mask_bytes, prompt, model, file_storage, mime_type=mime_type,
            owner_user_id=owner_user_id, team_id=team_id,
        )
    logger.warning("Provider %s 不支持图像编辑", model.provider)
    return None


async def _edit_openai_image(
        image_bytes: bytes,
        mask_bytes: bytes,
        prompt: str,
        model: LLMModel,
        file_storage: FileStorage,
        *,
        mime_type: str,
        owner_user_id: Optional[str],
        team_id: Optional[str],
) -> Optional[Tuple[bytes, str]]:
    base_url = str(model.base_url).rstrip("/")
    url = f"{base_url}/images/edits" if base_url.endswith("/v1") else f"{base_url}/v1/images/edits"
    headers = {"Authorization": f"Bearer {model.api_key}"}
    image_file = ("image.png", io.BytesIO(image_bytes), mime_type)
    mask_file = ("mask.png", io.BytesIO(mask_bytes), "image/png")
    data = {
        "model": model.extra_params.get("image_edit_model", "gpt-image-1"),
        "prompt": prompt,
        "n": "1",
        "size": "1024x1024",
        "response_format": "b64_json",
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                url,
                headers=headers,
                data=data,
                files={"image": image_file, "mask": mask_file},
            )
            response.raise_for_status()
            payload = response.json()
            b64 = payload["data"][0].get("b64_json", "")
            if not b64:
                return None
            edited = base64.b64decode(b64)
            await upload_image_bytes_to_storage(
                file_storage, edited, "image/png",
                owner_user_id=owner_user_id,
                team_id=team_id,
                fallback_to_proxy=True,
            )
            return edited, "image/png"
    except Exception as exc:
        logger.error("OpenAI 图像编辑失败: %s", exc)
        return None
