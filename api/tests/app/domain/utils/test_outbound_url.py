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


def test_outbound_url_rejects_nat64_wellknown_prefix(monkeypatch):
    # 64:ff9b::a9fe:a9fe embeds 169.254.169.254; Python calls it "global".
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("64:ff9b::a9fe:a9fe"),
    )

    with pytest.raises(OutboundURLRejected, match="NAT64"):
        resolve_outbound_url("https://example.com/api")


def test_outbound_url_rejects_ipv4_mapped_metadata_address(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("::ffff:169.254.169.254"),
    )

    with pytest.raises(OutboundURLRejected, match="元数据"):
        resolve_outbound_url("https://example.com/api")


def test_outbound_url_rejects_ipv4_mapped_private_address(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("::ffff:10.0.0.1"),
    )

    with pytest.raises(OutboundURLRejected, match="内网"):
        resolve_outbound_url("https://example.com/api")


def test_outbound_url_rejects_6to4_relay_anycast(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: _answers("192.88.99.1"),
    )

    with pytest.raises(OutboundURLRejected):
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


def test_allowlisted_host_may_resolve_into_docker_desktop_benchmark_range(monkeypatch):
    """Docker Desktop allocates container networks from 198.18.0.0/15; an
    explicitly allowlisted internal hostname resolving there must pass, while
    non-allowlisted hosts in the same range stay rejected."""
    import pytest

    from app.domain.utils import outbound_url as module

    monkeypatch.setattr(
        module,
        "_resolve_addresses",
        lambda hostname, port, resolver: ("198.18.1.112",),
    )
    resolved = module.resolve_outbound_url(
        "http://opencitadel-ops-collector:8090/mcp",
        allowed_ports=(8090,),
        allow_private_hosts=("opencitadel-ops-collector",),
    )
    assert resolved is not None

    with pytest.raises(module.OutboundURLRejected):
        module.resolve_outbound_url(
            "http://evil.example.com:8090/",
            allowed_ports=(8090,),
            allow_private_hosts=(),
        )
