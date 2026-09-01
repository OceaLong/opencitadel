"""Security coverage for the public artifact share link (G1 / E5).

``GET /share/artifact/{token}`` (``artifact_routes.py``) is an unauthenticated
public link. The share token *is* the capability: there is no session/tenant
scope on the read path, so the correctness of expiry, revocation and per-token
isolation is what keeps it safe.

Two layers are exercised:

* Service layer (``ArtifactService.get_by_share_token``) runs the real expiry
  check against a fake repository that resolves a token exactly the way the DB
  does (``WHERE share_token = :token``). This covers valid / expired / unknown /
  revoked tokens and cross-artifact isolation.
* Endpoint layer mounts only ``share_router`` (plus the shared exception
  handlers) to pin the HTTP contract: HTML is sanitised before it is served, a
  bad/expired token is a 404, and -- E5 -- the full ``share_token`` is never
  echoed back in the response body.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.artifact_service import ArtifactService
from app.domain.models.artifact import Artifact
from app.interfaces.endpoints.artifact_routes import share_router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_artifact_service


class _FakeArtifactRepo:
    """Resolves a share token the way the DB repo does: exact match on the
    currently-stored ``share_token`` (``None`` never matches)."""

    def __init__(self, artifacts: list[Artifact]) -> None:
        self._artifacts = artifacts

    async def get_by_share_token(self, token: str) -> Artifact | None:
        for artifact in self._artifacts:
            if artifact.share_token is not None and artifact.share_token == token:
                return artifact
        return None


def _make_service(artifacts: list[Artifact]) -> ArtifactService:
    repo = _FakeArtifactRepo(artifacts)

    def _uow_factory():
        @asynccontextmanager
        async def _cm():
            yield SimpleNamespace(artifact=repo)

        return _cm()

    return ArtifactService(uow_factory=_uow_factory, object_storage=None)


def _artifact(**overrides) -> Artifact:
    defaults = {
        "id": "art-1",
        "session_id": "sess-1",
        "kind": "web",
        "title": "Report",
        "share_token": "valid-token",
        "share_expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    defaults.update(overrides)
    return Artifact(**defaults)


# --------------------------------------------------------------------------- #
# Service layer: token resolution + expiry
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_valid_token_returns_artifact():
    service = _make_service([_artifact()])

    result = await service.get_by_share_token("valid-token")

    assert result is not None
    assert result.id == "art-1"


@pytest.mark.asyncio
async def test_expired_token_is_rejected():
    service = _make_service([_artifact(share_expires_at=datetime.now(UTC) - timedelta(seconds=1))])

    assert await service.get_by_share_token("valid-token") is None


@pytest.mark.asyncio
async def test_unknown_token_is_rejected():
    service = _make_service([_artifact()])

    assert await service.get_by_share_token("no-such-token") is None


@pytest.mark.asyncio
async def test_revoked_token_is_rejected():
    # After revocation share_token is cleared, so the previously issued link
    # resolves to nothing.
    revoked = _artifact(share_token=None)
    service = _make_service([revoked])

    assert await service.get_by_share_token("valid-token") is None


@pytest.mark.asyncio
async def test_tokens_are_isolated_per_artifact():
    # A token minted for artifact B must never surface artifact A (no cross
    # session/tenant access via a mismatched capability token).
    art_a = _artifact(id="art-a", session_id="sess-a", share_token="token-a")
    art_b = _artifact(id="art-b", session_id="sess-b", share_token="token-b")
    service = _make_service([art_a, art_b])

    assert (await service.get_by_share_token("token-a")).id == "art-a"
    assert (await service.get_by_share_token("token-b")).id == "art-b"


# --------------------------------------------------------------------------- #
# Endpoint layer: HTTP contract, sanitisation, no-token-leak
# --------------------------------------------------------------------------- #


@pytest.fixture
def share_client():
    service = AsyncMock()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(share_router)
    app.dependency_overrides[get_artifact_service] = lambda: service
    with TestClient(app) as client:
        yield client, service


def test_endpoint_valid_token_serves_sanitised_content(share_client):
    client, service = share_client
    artifact = _artifact(kind="web")
    service.get_by_share_token.return_value = artifact
    service.get_content_text.return_value = ("<p>hello</p>", False)

    resp = client.get("/share/artifact/valid-token")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["content"] == "<p>hello</p>"
    assert body["data"]["content_type"] == "text/html"
    # The public preview path must strip active content: sanitize_html=True.
    _args, kwargs = service.get_content_text.call_args
    assert kwargs.get("sanitize_html") is True


def test_endpoint_invalid_or_expired_token_returns_404(share_client):
    client, service = share_client
    service.get_by_share_token.return_value = None

    resp = client.get("/share/artifact/bogus")

    assert resp.status_code == 404
    # get_content_text is never reached for an unresolved token.
    service.get_content_text.assert_not_awaited()


def test_endpoint_response_never_leaks_full_share_token(share_client):
    # E5 de-identification: the full capability token must not appear anywhere in
    # the public response body.
    client, service = share_client
    secret_token = "super-secret-full-share-token-value"
    service.get_by_share_token.return_value = _artifact(share_token=secret_token)
    service.get_content_text.return_value = ("content", False)

    resp = client.get(f"/share/artifact/{secret_token}")

    assert resp.status_code == 200
    assert secret_token not in resp.text
