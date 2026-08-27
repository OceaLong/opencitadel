import pytest

from app.domain.errors import BadRequestError
from app.domain.runtime_policy import SourceAccessPolicy
from app.domain.services.knowledge_base.url_guard import validate_public_url

POLICY = SourceAccessPolicy()


def test_validate_public_url_rejects_file_scheme():
    with pytest.raises(BadRequestError):
        validate_public_url("file:///etc/passwd", policy=POLICY)


def test_validate_public_url_rejects_localhost():
    with pytest.raises(BadRequestError):
        validate_public_url("http://127.0.0.1/admin", policy=POLICY)


def test_validate_public_url_rejects_metadata_ip():
    with pytest.raises(BadRequestError):
        validate_public_url("http://169.254.169.254/latest/meta-data/", policy=POLICY)


def test_validate_public_url_accepts_public_https(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    assert (
        validate_public_url("https://example.com/docs", policy=POLICY) == "https://example.com/docs"
    )


def test_validate_public_url_rejects_credentials_and_docker_port():
    with pytest.raises(BadRequestError):
        validate_public_url("https://user:password@example.com/docs", policy=POLICY)
    with pytest.raises(BadRequestError):
        validate_public_url(
            "http://example.com:2375/containers/json",
            policy=POLICY,
        )


def test_validate_public_url_enforces_exact_operations_lists(monkeypatch):
    monkeypatch.setattr(
        "app.domain.utils.outbound_url.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    with pytest.raises(BadRequestError):
        validate_public_url(
            "https://blocked.example/docs",
            policy=SourceAccessPolicy(url_denylist=("blocked.example",)),
        )
    with pytest.raises(BadRequestError):
        validate_public_url(
            "https://other.example/docs",
            policy=SourceAccessPolicy(url_allowlist=("allowed.example",)),
        )
