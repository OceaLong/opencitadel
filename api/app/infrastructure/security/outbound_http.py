"""HTTP transport that validates DNS and connects to the validated IP address."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.utils.outbound_url import (
    DEFAULT_OUTBOUND_PORTS,
    OutboundURLRejected,
    resolve_outbound_url,
)

DEFAULT_OUTBOUND_NETWORK_POLICY = OutboundNetworkPolicy(
    allowed_ports=DEFAULT_OUTBOUND_PORTS,
)


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
            allowed_ports if allowed_ports is not None else DEFAULT_OUTBOUND_PORTS
        )
        self._allow_private_hosts = tuple(
            allow_private_hosts if allow_private_hosts is not None else ()
        )

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore backend interface)
        local_address: str | None = None,
        socket_options=None,
    ):
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        # DNS is resolved on the event loop's default executor rather than
        # synchronously, so a slow or hostile resolver cannot block the shared
        # async worker (DoS). resolve_outbound_url still performs every URL,
        # port and address check and pins the connection to a validated IP; the
        # prefetched answers are handed to it via the resolver injection point,
        # keeping the DNS-pinning semantics (resolve once, validate all answers,
        # connect by IP, SNI stays the original host) unchanged.
        resolver = await self._prefetch_resolver(host, port)
        target = resolve_outbound_url(
            f"http://{url_host}:{port}",
            allowed_ports=self._allowed_ports,
            allow_private_hosts=self._allow_private_hosts,
            resolver=resolver,
        )
        return await self._inner.connect_tcp(
            target.addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def _prefetch_resolver(self, host: str, port: int):
        """Resolve DNS off the event loop and return a resolver returning it.

        IP literals need no lookup, so ``None`` is returned and
        resolve_outbound_url handles the literal directly. Lookup failures are
        replayed by the closure so resolve_outbound_url wraps them into its
        standard OutboundURLRejected error after its own port/scheme checks.
        """
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass

        loop = asyncio.get_running_loop()
        answers: list[tuple] | None = None
        error: OSError | None = None
        try:
            answers = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as exc:
            error = exc

        def _resolver(*_args: Any, **_kwargs: Any):
            if error is not None:
                raise error
            return answers

        return _resolver

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
    outbound_policy: OutboundNetworkPolicy | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Create an HTTP client whose sockets can only reach validated targets."""
    policy = outbound_policy or DEFAULT_OUTBOUND_NETWORK_POLICY
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(30.0),
        auth=auth,
        follow_redirects=follow_redirects,
        transport=SSRFProtectedAsyncHTTPTransport(
            allowed_ports=allowed_ports or policy.allowed_ports,
            allow_private_hosts=allow_private_hosts or policy.allow_private_hosts,
        ),
        trust_env=False,
        **kwargs,
    )


def create_ssrf_safe_mcp_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    *,
    outbound_policy: OutboundNetworkPolicy | None = None,
) -> httpx.AsyncClient:
    return create_ssrf_safe_async_client(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=True,
        outbound_policy=outbound_policy,
    )
