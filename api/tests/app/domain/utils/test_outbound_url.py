import pytest

from app.domain.utils.outbound_url import (
    OutboundURLRejected,
    resolve_outbound_url,
)


def _answers(*addresses: str):
    return [(2, 1, 6, "", (address, 443)) for address in addresses]


def test_outbound_url_rejects_credentials_and_unapproved_ports():
    with pytest.raises(OutboundURLRejected, match="凭据"):
        resolve_outbound_url("https://user:secret@example.com/api")

    with pytest.raises(OutboundURLRejected, match="端口"):
        resolve_outbound_url("https://example.com:2375/api")


def test_outbound_url_rejects_local_and_metadata_hostnames():
    for url in (
        "http://localhost/admin",
        "http://service.internal/admin",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        with pytest.raises(OutboundURLRejected):
            resolve_outbound_url(url)


def test_exact_allowlist_permits_rfc1918_internal_service(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("172.17.0.1"),
    )

    target = resolve_outbound_url(
        "http://host.docker.internal:11434/v1",
        allowed_ports={11434},
        allow_private_hosts={"host.docker.internal"},
    )

    assert target.addresses == ("172.17.0.1",)


def test_private_allowlist_never_overrides_metadata_address_block(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("169.254.169.254"),
    )

    with pytest.raises(OutboundURLRejected, match="元数据"):
        resolve_outbound_url(
            "http://approved.example/api",
            allow_private_hosts={"approved.example"},
        )


def test_outbound_url_rejects_any_unsafe_dns_answer(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("93.184.216.34", "127.0.0.1"),
    )

    with pytest.raises(OutboundURLRejected, match="内网"):
        resolve_outbound_url("https://example.com/api")


def test_outbound_url_returns_all_validated_public_addresses(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("93.184.216.34", "2606:2800:220:1::"),
    )

    target = resolve_outbound_url("https://example.com/api")

    assert target.url == "https://example.com/api"
    assert target.hostname == "example.com"
    assert target.port == 443
    assert target.addresses == ("93.184.216.34", "2606:2800:220:1::")
