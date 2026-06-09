#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any, Union, AsyncGenerator

from openai import AsyncOpenAI

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.app_config import LLMConfig
from app.domain.models.llm_model import LLMModel, ModelCapabilities
from app.infrastructure.external.llm.base_llm import (
    MultimodalFallbackMixin,
    _has_multimodal_image_content,
    _strip_multimodal_to_text,
    is_retriable_multimodal_error,
)
from app.infrastructure.external.llm.base_llm import normalize_usage
from app.infrastructure.external.llm.retry import with_llm_retry
from app.infrastructure.observability.llm_metrics import record_multimodal_request

logger = logging.getLogger(__name__)

_DEFAULT_TOOL_MODEL = "deepseek-chat"
_DEFAULT_REQUEST_TIMEOUT = 300
_CLIENT_EXTRA_KEYS = frozenset({
    "base_url",
    "api_key",
    "tool_model_name",
    "omit_parallel_tool_calls",
    "thinking_request_params",
    "thinking_extra_body",
    "thinking_model_name",
    "request_timeout",
})
_THINKING_CONFIG_KEYS = frozenset({
    "thinking_request_params",
    "thinking_extra_body",
    "thinking_model_name",
})


def _resolve_request_model(
        model_name: str,
        tools: List[Dict[str, Any]] | None,
        extra: Dict[str, Any],
        thinking_enabled: bool = False,
) -> str:
    if tools:
        if model_name == "deepseek-reasoner" or model_name.endswith("-reasoner"):
            return str(extra.get("tool_model_name") or _DEFAULT_TOOL_MODEL)
        return model_name
    if thinking_enabled:
        thinking_model = extra.get("thinking_model_name")
        if thinking_model:
            return str(thinking_model)
    return model_name


def _merge_thinking_request_kwargs(
        request_kwargs: Dict[str, Any],
        extra: Dict[str, Any],
        thinking_enabled: bool,
) -> None:
    if not thinking_enabled:
        return
    thinking_params = extra.get("thinking_request_params")
    if isinstance(thinking_params, dict):
        request_kwargs.update(thinking_params)
    thinking_body = extra.get("thinking_extra_body")
    if isinstance(thinking_body, dict):
        existing = request_kwargs.get("extra_body")
        if not isinstance(existing, dict):
            existing = {}
        request_kwargs["extra_body"] = {**existing, **thinking_body}


def _format_llm_error(error: Exception, model_name: str) -> str:
    message = str(error)
    if "Not supported model" in message:
        return (
            f"调用LLM失败: 模型 {model_name} 不被当前 Base URL 支持，"
            f"请在模型管理中检查 Model Name。原始错误: {message}"
        )
    return f"调用LLM失败: {message}"


def _log_response_summary(response: Any, request_model: str) -> None:
    usage = getattr(response, "usage", None)
    usage_text = usage.model_dump() if usage is not None and hasattr(usage, "model_dump") else None
    finish_reason = response.choices[0].finish_reason if response.choices else None
    logger.info(
        f"OpenAI客户端返回摘要: model={request_model} finish_reason={finish_reason} usage={usage_text}"
    )


def _resolve_request_timeout(extra: Dict[str, Any]) -> float:
    configured = extra.get("request_timeout")
    if configured is None:
        return float(_DEFAULT_REQUEST_TIMEOUT)
    try:
        timeout = float(configured)
        return timeout if timeout > 0 else float(_DEFAULT_REQUEST_TIMEOUT)
    except (TypeError, ValueError):
        logger.warning("无效的 request_timeout=%s，回退默认值 %s", configured, _DEFAULT_REQUEST_TIMEOUT)
        return float(_DEFAULT_REQUEST_TIMEOUT)


def _log_multimodal_request_summary(messages: List[Dict[str, Any]]) -> None:
    from app.domain.utils.vision_tokens import count_message_image_stats

    image_count, image_bytes, _ = count_message_image_stats(messages)
    if image_count:
        record_multimodal_request(image_bytes=image_bytes, image_count=image_count)
        logger.info(
            "OpenAI多模态请求摘要: message_count=%s image_part_count=%s image_bytes=%s",
            len(messages),
            image_count,
            image_bytes,
        )


class OpenAILLM(MultimodalFallbackMixin, LLM):
    """基于OpenAI SDK/兼容OpenAI格式的LLM调用类（支持OpenAI/Ollama/Azure）"""

    def __init__(
            self,
            config: Union[LLMModel, LLMConfig],
            thinking_enabled: bool = False,
            **kwargs,
    ) -> None:
        if isinstance(config, LLMModel):
            base_url = config.base_url
            api_key = config.api_key
            model_name = config.model_name
            temperature = config.temperature
            max_tokens = config.max_tokens
            extra = config.extra_params or {}
            self._capabilities = config.capabilities
            self._supports_multimodal = config.supports_multimodal
        else:
            base_url = str(config.base_url)
            api_key = config.api_key
            model_name = config.model_name
            temperature = config.temperature
            max_tokens = config.max_tokens
            extra = {}
            self._capabilities = ModelCapabilities()
            self._supports_multimodal = False

        self._extra_params = extra
        self._thinking_enabled = thinking_enabled
        if thinking_enabled and not any(key in extra for key in _THINKING_CONFIG_KEYS):
            logger.warning(
                f"会话已开启思考模式，但模型[{model_name}]未配置 thinking 参数模板，将按普通模式请求"
            )
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "sk-placeholder",
            **{k: v for k, v in extra.items() if k not in _CLIENT_EXTRA_KEYS},
            **kwargs,
        )
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = _resolve_request_timeout(extra)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def supports_multimodal(self) -> bool:
        return self._supports_multimodal

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    async def _create_chat_completion(
            self,
            request_kwargs: Dict[str, Any],
            tools: List[Dict[str, Any]] | None,
            tool_choice: str | None,
            request_model: str,
    ) -> Any:
        if tools:
            logger.info(
                f"调用OpenAI客户端向LLM发起请求并携带工具信息: {request_model}"
                + (f" (配置模型: {self._model_name})" if request_model != self._model_name else "")
                + (f" thinking={self._thinking_enabled}" if self._thinking_enabled else "")
                + f" timeout={self._timeout}s"
            )
            tool_kwargs: Dict[str, Any] = {
                "tools": tools,
                "tool_choice": tool_choice,
            }
            if not self._extra_params.get("omit_parallel_tool_calls"):
                tool_kwargs["parallel_tool_calls"] = True
            return await with_llm_retry(
                lambda: self._client.chat.completions.create(
                    **request_kwargs,
                    **tool_kwargs,
                )
            )

        logger.info(
            f"调用OpenAI客户端向LLM发起请求未携带工具: {request_model}"
            + (f" thinking={self._thinking_enabled}" if self._thinking_enabled else "")
            + f" timeout={self._timeout}s"
        )
        return await with_llm_retry(
            lambda: self._client.chat.completions.create(**request_kwargs)
        )

    def _raise_llm_error(self, error: Exception, request_model: str) -> None:
        error_text = str(error).lower()
        if "timeout" in error_text or "timed out" in error_text:
            logger.error(
                "调用OpenAI客户端超时: model=%s timeout=%ss error=%s",
                request_model,
                self._timeout,
                error,
            )
            raise ServerRequestsError(
                f"调用LLM超时(>{self._timeout}s): 请检查模型服务或减小多模态图片体积"
            )
        logger.error(f"调用OpenAI客户端发生错误: {str(error)}", exc_info=True)
        raise ServerRequestsError(_format_llm_error(error, request_model))

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> Dict[str, Any]:
        request_model = _resolve_request_model(
            self._model_name,
            tools,
            self._extra_params,
            thinking_enabled=self._thinking_enabled,
        )
        request_kwargs: Dict[str, Any] = {
            "model": request_model,
            "messages": messages,
            "timeout": self._timeout,
        }
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature
        if self._max_tokens is not None and self._max_tokens > 0:
            request_kwargs["max_tokens"] = self._max_tokens
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        _merge_thinking_request_kwargs(
            request_kwargs,
            self._extra_params,
            thinking_enabled=self._thinking_enabled,
        )
        _log_multimodal_request_summary(messages)

        async def _create_with_kwargs(kwargs: Dict[str, Any]):
            return await self._create_chat_completion(
                kwargs,
                tools,
                tool_choice,
                request_model,
            )

        try:
            response = await _create_with_kwargs(request_kwargs)
        except ServerRequestsError:
            raise
        except Exception as error:
            if _has_multimodal_image_content(messages) and is_retriable_multimodal_error(error):
                try:
                    response = await self._apply_multimodal_fallback(
                        error,
                        request_kwargs,
                        _create_with_kwargs,
                    )
                except Exception as retry_error:
                    if retry_error is error:
                        self._raise_llm_error(error, request_model)
                    self._raise_llm_error(retry_error, request_model)
            else:
                self._raise_llm_error(error, request_model)

        if not response.choices:
            raise ServerRequestsError("调用LLM失败: 响应 choices 为空")
        message = response.choices[0].message
        if message is None:
            raise ServerRequestsError("调用LLM失败: 响应 message 为空")
        _log_response_summary(response, request_model)
        result = message.model_dump()
        usage = getattr(response, "usage", None)
        if usage is not None:
            raw = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
            result["_usage"] = normalize_usage(raw)
        return result

    async def stream_invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        request_model = _resolve_request_model(
            self._model_name,
            tools,
            self._extra_params,
            thinking_enabled=self._thinking_enabled,
        )
        request_kwargs: Dict[str, Any] = {
            "model": request_model,
            "messages": messages,
            "timeout": self._timeout,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature
        if self._max_tokens is not None and self._max_tokens > 0:
            request_kwargs["max_tokens"] = self._max_tokens
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        _merge_thinking_request_kwargs(
            request_kwargs,
            self._extra_params,
            thinking_enabled=self._thinking_enabled,
        )
        _log_multimodal_request_summary(messages)

        tool_kwargs: Dict[str, Any] = {}
        if tools:
            tool_kwargs = {
                "tools": tools,
                "tool_choice": tool_choice,
            }
            if not self._extra_params.get("omit_parallel_tool_calls"):
                tool_kwargs["parallel_tool_calls"] = True

        try:
            stream = await with_llm_retry(
                lambda: self._client.chat.completions.create(
                    **request_kwargs,
                    **tool_kwargs,
                )
            )
        except ServerRequestsError:
            raise
        except Exception as error:
            if _has_multimodal_image_content(messages) and is_retriable_multimodal_error(error):
                async def _create_stream_with_kwargs(kwargs: Dict[str, Any]):
                    return await self._client.chat.completions.create(
                        **kwargs,
                        **tool_kwargs,
                    )

                try:
                    stream = await self._apply_multimodal_fallback(
                        error,
                        request_kwargs,
                        _create_stream_with_kwargs,
                    )
                except Exception as retry_error:
                    if retry_error is error:
                        self._raise_llm_error(error, request_model)
                    self._raise_llm_error(retry_error, request_model)
            else:
                self._raise_llm_error(error, request_model)

        stream_usage: Dict[str, int] = {}
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                raw = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
                stream_usage = normalize_usage(raw)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            payload: Dict[str, Any] = {}
            if delta.content:
                payload["content"] = delta.content
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                payload["reasoning_content"] = reasoning
            if delta.tool_calls:
                payload["tool_calls"] = []
                for tool_call in delta.tool_calls:
                    payload["tool_calls"].append({
                        "index": tool_call.index,
                        "id": tool_call.id,
                        "function": {
                            "name": tool_call.function.name if tool_call.function else None,
                            "arguments": tool_call.function.arguments if tool_call.function else "",
                        },
                    })
            if payload:
                yield payload
        if stream_usage.get("total_tokens"):
            yield {"usage": stream_usage}
