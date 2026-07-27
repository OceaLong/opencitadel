#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""HTTP transport that validates DNS and connects to the validated IP address."""
from __future__ import annotations

import ssl
from typing import Any, Iterable

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from app.domain.utils.outbound_url import (
    DEFAULT_OUTBOUND_PORTS,
    resolve_outbound_url,
)
from core.config import get_settings


def _configured_private_hosts() -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in get_settings().outbound_private_host_allowlist.split(",")
        if item.strip()
    )


def _configured_ports() -> frozenset[int]:
    values = {
        int(item.strip())
        for item in get_settings().outbound_allowed_ports.split(",")
        if item.strip()
    }
    return frozenset(values or DEFAULT_OUTBOUND_PORTS)


class SSRFProtectedAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, validate every answer, and connect by IP to prevent rebinding."""

    def __init__(
        self,
        *,
        inner: httpcore.AsyncNetworkBackend | None = None,
        allowed_ports: Iterable[int] | None = None,
        allow_private_hosts: Iterable[str] | None = None,
    ) -> None:
        self._inner = inner or AutoBackend()
        self._allowed_ports = frozenset(
            allowed_ports if allowed_ports is not None else _configured_ports()
        )
        self._allow_private_hosts = tuple(
            allow_private_hosts
            if allow_private_hosts is not None
            else _configured_private_hosts()
        )

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        target = resolve_outbound_url(
            f"http://{url_host}:{port}",
            allowed_ports=self._allowed_ports,
            allow_private_hosts=self._allow_private_hosts,
        )
        return await self._inner.connect_tcp(
            target.addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *args, **kwargs):
        raise OutboundURLRejected("出站 HTTP 不允许 Unix Socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _AsyncResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream) -> None:
        self._stream = stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        if hasattr(self._stream, "aclose"):
            await self._stream.aclose()


class SSRFProtectedAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Minimal HTTPX transport backed by the DNS-pinning network backend."""

    def __init__(
        self,
        *,
        allowed_ports: Iterable[int] | None = None,
        allow_private_hosts: Iterable[str] | None = None,
    ) -> None:
        ssl_context = ssl.create_default_context()
        backend = SSRFProtectedAsyncNetworkBackend(
            allowed_ports=allowed_ports,
            allow_private_hosts=allow_private_hosts,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            network_backend=backend,
            max_connections=20,
            max_keepalive_connections=10,
            keepalive_expiry=5.0,
        )

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        core_response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_AsyncResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


def create_ssrf_safe_async_client(
    *,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    auth: httpx.Auth | None = None,
    follow_redirects: bool = False,
    allowed_ports: Iterable[int] | None = None,
    allow_private_hosts: Iterable[str] | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTP client whose sockets can only reach validated targets."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30.0),
        auth=auth,
        follow_redirects=follow_redirects,
        transport=SSRFProtectedAsyncHTTPTransport(
            allowed_ports=allowed_ports,
            allow_private_hosts=allow_private_hosts,
        ),
        trust_env=False,
        **kwargs,
    )


def create_ssrf_safe_mcp_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return create_ssrf_safe_async_client(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=True,
    )


# Local import alias kept at module end so tests and callers receive the same
# domain exception without introducing an infrastructure-specific error type.
from app.domain.utils.outbound_url import OutboundURLRejected  # noqa: E402
