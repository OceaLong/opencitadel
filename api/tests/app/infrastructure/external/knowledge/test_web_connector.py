"""Infrastructure KB connectors must revalidate every redirect hop."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.domain.errors import BadRequestError
from app.domain.runtime_policy import (
    OperationsPolicy,
    RuntimePolicyStaleError,
    SourceAccessPolicy,
)
from app.infrastructure.external.knowledge import web_connector
from tests.runtime_policy_support import MutablePolicyReader

POLICY = SourceAccessPolicy()


async def _policy_provider() -> SourceAccessPolicy:
    return POLICY


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, headers=None):
        self.calls.append((url, headers))
        return self.responses.pop(0)


class _ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _response(status, url, *, location=None, body=""):
    headers = {"location": location} if location else {}
    return httpx.Response(
        status,
        headers=headers,
        text=body,
        request=httpx.Request("GET", url),
    )


@pytest.mark.parametrize(
    "fetcher",
    [
        web_connector.fetch_web_document,
        web_connector.fetch_confluence_document,
    ],
)
@pytest.mark.asyncio
async def test_typed_connector_revalidates_public_redirect_hops(
    monkeypatch,
    fetcher,
):
    client = _Client(
        [
            _response(
                302,
                "https://example.com/start",
                location="/final",
            ),
            _response(
                200,
                "https://example.com/final",
                body="<html><h1>Final</h1><main>content</main></html>",
            ),
        ]
    )
    validated = []

    def validate(url, *, policy):
        assert policy is POLICY
        validated.append(url)
        return url

    monkeypatch.setattr(web_connector, "validate_public_url", validate)
    monkeypatch.setattr(
        web_connector,
        "create_ssrf_safe_async_client",
        lambda **_kwargs: _ClientContext(client),
    )

    document = await fetcher(
        "https://example.com/start",
        policy_provider=_policy_provider,
    )

    assert document.title == "Final"
    assert [url for url, _headers in client.calls] == [
        "https://example.com/start",
        "https://example.com/final",
    ]
    assert "https://example.com/final" in validated


@pytest.mark.parametrize(
    "fetcher",
    [
        web_connector.fetch_web_document,
        web_connector.fetch_confluence_document,
    ],
)
@pytest.mark.asyncio
async def test_typed_connector_blocks_private_redirect_before_second_request(
    monkeypatch,
    fetcher,
):
    client = _Client(
        [
            _response(
                302,
                "https://example.com/start",
                location="http://169.254.169.254/latest/meta-data",
            )
        ]
    )

    def validate(url, *, policy):
        assert policy is POLICY
        if "169.254.169.254" in url:
            raise BadRequestError("private redirect blocked")
        return url

    monkeypatch.setattr(web_connector, "validate_public_url", validate)
    monkeypatch.setattr(
        web_connector,
        "create_ssrf_safe_async_client",
        lambda **_kwargs: _ClientContext(client),
    )

    with pytest.raises(BadRequestError, match="private redirect blocked"):
        await fetcher(
            "https://example.com/start",
            policy_provider=_policy_provider,
        )
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_feishu_connector_uses_same_ssrf_safe_web_path(monkeypatch):
    safe_fetch = AsyncMock(
        return_value=web_connector.WebDocument(
            title="safe",
            content="content",
        )
    )
    monkeypatch.setattr(web_connector, "fetch_web_document", safe_fetch)

    result = await web_connector.fetch_feishu_document(
        "https://example.com/doc",
        policy_provider=_policy_provider,
    )

    assert result.title == "safe"
    safe_fetch.assert_awaited_once_with(
        "https://example.com/doc",
        policy_provider=_policy_provider,
    )


@pytest.mark.asyncio
async def test_gateway_denies_fetch_when_operations_policy_is_stale() -> None:
    reader = MutablePolicyReader()
    reader.error = RuntimePolicyStaleError(age_seconds=61)
    gateway = web_connector.HttpWebDocumentGateway(policy_reader=reader)

    with pytest.raises(RuntimePolicyStaleError):
        await gateway.fetch(web_connector.KBSourceType.WEB, "https://example.com/doc")

    assert reader.operations_calls[0][0] is True


@pytest.mark.asyncio
async def test_gateway_applies_live_source_denylist_before_http(monkeypatch) -> None:
    reader = MutablePolicyReader(
        operations=OperationsPolicy(
            source_access=SourceAccessPolicy(url_denylist=("blocked.example",))
        )
    )
    client_factory = AsyncMock()
    monkeypatch.setattr(
        web_connector,
        "create_ssrf_safe_async_client",
        client_factory,
    )
    gateway = web_connector.HttpWebDocumentGateway(policy_reader=reader)

    with pytest.raises(BadRequestError):
        await gateway.fetch(
            web_connector.KBSourceType.WEB,
            "https://blocked.example/doc",
        )

    client_factory.assert_not_called()
    assert reader.operations_calls[0][0] is True


@pytest.mark.asyncio
async def test_redirect_hop_rechecks_policy_before_second_http_request(monkeypatch) -> None:
    reader = MutablePolicyReader()

    def validate(url, *, policy):
        if "blocked.example" in url and "blocked.example" in policy.url_denylist:
            raise BadRequestError("live redirect policy blocked the destination")
        return url

    class _TighteningClient(_Client):
        async def get(self, url, headers=None):
            response = await super().get(url, headers=headers)
            reader.set_operations(
                OperationsPolicy(
                    source_access=SourceAccessPolicy(url_denylist=("blocked.example",))
                )
            )
            return response

    client = _TighteningClient(
        [
            _response(
                302,
                "https://example.com/start",
                location="https://blocked.example/final",
            )
        ]
    )
    monkeypatch.setattr(
        web_connector,
        "create_ssrf_safe_async_client",
        lambda **_kwargs: _ClientContext(client),
    )
    monkeypatch.setattr(web_connector, "validate_public_url", validate)
    gateway = web_connector.HttpWebDocumentGateway(policy_reader=reader)

    with pytest.raises(BadRequestError):
        await gateway.fetch(
            web_connector.KBSourceType.WEB,
            "https://example.com/start",
        )

    assert len(client.calls) == 1
    assert [fresh for fresh, _now in reader.operations_calls] == [True, True]
