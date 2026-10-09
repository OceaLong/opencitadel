"""Exactly one guard permit per adapter transport send, including fallbacks."""

import json
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit

import httpx
from openai import APIStatusError

from app.application.ports.inference_dispatch import current_dispatch
from app.infrastructure.external.llm.base_llm import normalize_usage


async def _physical_identity(guard, model, payload):
    if guard is None:
        return None
    permit = await guard.before_send(model, payload)
    # New durable guards expose a one-use permit only after commit. Existing
    # F07-only guards remain compatible during the explicit composition rollout.
    return permit.consume() if hasattr(permit, "consume") else permit


# Conclusive native rejections release only physical occupancy. They are not
# statements of zero billing. Timeouts/conflicts/server errors remain unknown.
_NATIVE_REJECTIONS = {
    "openai": ("api.openai.com", {400, 401, 403, 404, 422, 429}),
    "anthropic": ("api.anthropic.com", {400, 401, 403, 404, 413, 429}),
    "gemini": ("generativelanguage.googleapis.com", {400, 403, 404, 429}),
}


def _native_rejection(response, model, provider):
    native = _NATIVE_REJECTIONS.get(provider)
    if native is None or model is None or model.provider.value != provider:
        return False
    if not isinstance(response, httpx.Response) or response.status_code not in native[1]:
        return False
    try:
        actual = urlsplit(str(response.request.url))
    except RuntimeError:
        # A response without its originating request cannot establish a
        # conclusive native rejection; keep the dispatch outcome unknown.
        return False
    configured = urlsplit(model.base_url)
    if any(
        url.scheme != "https"
        or url.hostname != native[0]
        or url.port not in (None, 443)
        or url.username
        or url.password
        for url in (configured, actual)
    ):
        return False
    if provider == "openai":
        return configured.path.rstrip("/") in ("", "/v1") and actual.path == "/v1/chat/completions"
    if provider == "anthropic":
        return configured.path.rstrip("/") == "" and actual.path == "/v1/messages"
    return (
        configured.path.rstrip("/") == ""
        and actual.path.startswith("/v1beta/models/")
        and actual.path.endswith((":generateContent", ":streamGenerateContent"))
    )


async def _rejection_completion(guard, identity, model, provider, response):
    if (
        guard is not None
        and hasattr(guard, "after_completion")
        and _native_rejection(response, model, provider)
    ):
        await guard.after_completion(identity, None)


async def physical_send(send, payload, *, provider):
    guard, model = current_dispatch.get()
    identity = await _physical_identity(guard, model, payload)
    # Allocation is committed first. Any exception/cancellation/process death
    # leaves the durable intent unknown; no finally block is needed to invent it.
    try:
        response = await send()
    except APIStatusError as exc:
        await _rejection_completion(guard, identity, model, provider, exc.response)
        raise
    if isinstance(response, httpx.Response) and not response.is_success:
        await _rejection_completion(guard, identity, model, provider, response)
        return response
    if guard is not None:
        if hasattr(response, "__aiter__"):
            return SDKStream(response, StreamEvidence(guard, identity, provider, payload))
        if isinstance(response, dict):
            data = response
        elif hasattr(response, "model_dump"):
            data = response.model_dump()
        elif hasattr(response, "json"):
            try:
                data = response.json()
            except (ValueError, TypeError):
                data = {}
        else:
            # Streaming send: intent stays unknown unless stream accounting
            # is explicitly settled by its caller. Do not pretend free usage.
            data = {}
        raw = data.get("usageMetadata") if provider == "gemini" else data.get("usage")
        usage = normalize_usage(raw, provider=provider)
        revision = data.get("modelVersion") if provider == "gemini" else data.get("model")
        await guard.after_send(identity, usage, revision if isinstance(revision, str) else None)
    return response


class StreamEvidence:
    def __init__(self, guard, identity, provider, payload=None):
        payload = payload or {}
        requested = (
            payload.get("generationConfig", {}).get("candidateCount", 1)
            if provider == "gemini"
            else payload.get("n", 1)
        )
        self.expected_choices = requested if type(requested) is int and requested > 0 else None
        self.finished_choices = set()
        self.guard, self.identity, self.provider = guard, identity, provider
        self.raw, self.revision = {}, None
        self.terminal = self.final_usage = self.failed = self.recorded = False

    def observe(self, data):
        if not isinstance(data, dict):
            return
        if data.get("error") or data.get("type") == "error":
            self.failed = True
        if self.provider == "anthropic":
            self.terminal |= data.get("type") == "message_stop"
            self.final_usage |= (
                data.get("type") == "message_delta"
                and type((data.get("usage") or {}).get("output_tokens")) is int
            )
        elif self.provider == "gemini":
            self._observe_choices(data.get("candidates", []), "finishReason")
            self.final_usage |= self.terminal and isinstance(data.get("usageMetadata"), dict)
        else:
            self._observe_choices(data.get("choices", []), "finish_reason")
            self.final_usage |= (
                self.terminal and data.get("choices") == [] and isinstance(data.get("usage"), dict)
            )
        if self.provider == "anthropic" and isinstance(data.get("message"), dict):
            self.observe(data["message"])
        raw = data.get("usageMetadata") if self.provider == "gemini" else data.get("usage")
        if isinstance(raw, dict):
            self.raw.update(raw)
        revision = data.get("modelVersion") if self.provider == "gemini" else data.get("model")
        if isinstance(revision, str):
            self.revision = revision

    def _observe_choices(self, choices, finish_key):
        if self.expected_choices is None:
            return
        for choice in choices:
            if not isinstance(choice, dict) or not choice.get(finish_key):
                continue
            index = choice.get("index", 0 if self.expected_choices == 1 else None)
            if type(index) is int and 0 <= index < self.expected_choices:
                self.finished_choices.add(index)
        self.terminal = len(self.finished_choices) == self.expected_choices

    async def finish(self):
        if self.recorded or self.guard is None or not self.terminal or self.failed:
            return
        if self.final_usage:
            await self.guard.after_send(
                self.identity, normalize_usage(self.raw, provider=self.provider), self.revision
            )
        elif hasattr(self.guard, "after_completion"):
            await self.guard.after_completion(self.identity, self.revision)
        self.recorded = True


class SDKStream:
    def __init__(self, stream, evidence):
        self.stream, self.evidence = stream, evidence

    def __getattr__(self, key):
        return getattr(self.stream, key)

    async def __aiter__(self):
        async for chunk in self.stream:
            self.evidence.observe(chunk.model_dump() if hasattr(chunk, "model_dump") else chunk)
            yield chunk
        await self.evidence.finish()


class HTTPStream:
    def __init__(self, response, evidence):
        self.response, self.evidence = response, evidence

    def __getattr__(self, key):
        return getattr(self.response, key)

    async def aiter_lines(self):
        async for line in self.response.aiter_lines():
            if line.startswith("data:"):
                with suppress(ValueError, TypeError):
                    self.evidence.observe(json.loads(line[5:].strip()))
            yield line
        await self.evidence.finish()


@asynccontextmanager
async def physical_stream(client, url, *, payload, provider, headers=None):
    guard, model = current_dispatch.get()
    identity = await _physical_identity(guard, model, payload)
    kwargs = {"json": payload}
    if headers is not None:
        kwargs["headers"] = headers
    async with client.stream("POST", url, **kwargs) as response:
        if not response.is_success:
            await _rejection_completion(guard, identity, model, provider, response)
            yield response
            return
        yield (
            HTTPStream(response, StreamEvidence(guard, identity, provider, payload))
            if guard is not None
            else response
        )
