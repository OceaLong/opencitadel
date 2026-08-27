import pytest

from app.infrastructure.security.outbound_http import (
    SSRFProtectedAsyncNetworkBackend,
)


class _RecordingBackend:
    def __init__(self):
        self.host = None
        self.port = None
        self.stream = object()

    def connect_tcp(
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        return self._connect_tcp(host, port)

    async def _connect_tcp(self, host, port):
        self.host = host
        self.port = port
        return self.stream

    async def connect_unix_socket(self, *args, **kwargs):
        raise AssertionError("unix sockets are not permitted")

    async def sleep(self, seconds):
        return None


@pytest.mark.asyncio
async def test_network_backend_pins_connection_to_validated_ip(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    inner = _RecordingBackend()
    backend = SSRFProtectedAsyncNetworkBackend(inner=inner)

    stream = await backend.connect_tcp("example.com", 443)

    assert stream is inner.stream
    assert inner.host == "93.184.216.34"
    assert inner.port == 443


@pytest.mark.asyncio
async def test_network_backend_revalidates_and_rejects_rebound_dns(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("169.254.169.254", 80)),
        ],
    )
    backend = SSRFProtectedAsyncNetworkBackend(inner=_RecordingBackend())

    with pytest.raises(ValueError, match="内网"):
        await backend.connect_tcp("example.com", 80)


@pytest.mark.asyncio
async def test_network_backend_parses_ipv6_literals_without_url_ambiguity():
    inner = _RecordingBackend()
    backend = SSRFProtectedAsyncNetworkBackend(
        inner=inner,
        allow_private_hosts=("2001:4860:4860::8888",),
    )

    await backend.connect_tcp("2001:4860:4860::8888", 443)

    assert inner.host == "2001:4860:4860::8888"
    assert inner.port == 443
