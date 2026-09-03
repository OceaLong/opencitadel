"""Configurable search providers must fail loudly and honor SEARCH_PROVIDER.

The legacy Bing HTML scraper silently returned an empty result set on layout
changes or bot challenges, so the agent kept answering from nothing. These
tests pin the new contract: misconfiguration fails at composition time,
provider outages surface as ``success=False``, and ``none`` removes the tool.
"""

import asyncio

import httpx
import pytest

from app.domain.services.tools.tool_registry import ToolRegistry
from app.infrastructure.external.search import providers
from app.infrastructure.external.search.bing_search import (
    BingSearchEngine,
    _parse_bing_html,
)
from app.infrastructure.external.search.providers import (
    BingApiSearchEngine,
    SearxngSearchEngine,
    TavilySearchEngine,
    build_search_engine,
)


class _StubResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("GET", "http://test"), response=None
            )


class _StubClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def post(self, *args, **kwargs):
        return await self.get(*args, **kwargs)


def _patch_client(monkeypatch, response):
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: _StubClient(response))


def test_factory_resolves_each_provider():
    assert build_search_engine("none") is None
    assert isinstance(build_search_engine("bing_html"), BingSearchEngine)
    assert isinstance(
        build_search_engine("searxng", endpoint="http://searxng:8080"),
        SearxngSearchEngine,
    )
    assert isinstance(build_search_engine("tavily", api_key="k"), TavilySearchEngine)
    assert isinstance(build_search_engine("bing_api", api_key="k"), BingApiSearchEngine)


def test_factory_fails_fast_on_misconfiguration():
    with pytest.raises(ValueError, match="SEARCH_ENDPOINT"):
        build_search_engine("searxng")
    with pytest.raises(ValueError, match="SEARCH_API_KEY"):
        build_search_engine("tavily")
    with pytest.raises(ValueError, match="SEARCH_API_KEY"):
        build_search_engine("bing_api")
    with pytest.raises(ValueError, match="unknown SEARCH_PROVIDER"):
        build_search_engine("altavista")


def test_searxng_maps_results(monkeypatch):
    payload = {
        "results": [
            {"title": "T", "url": "https://a.test", "content": "S"},
            {"title": "no-url entries are dropped", "content": "x"},
        ]
    }
    _patch_client(monkeypatch, _StubResponse(payload))
    result = asyncio.run(SearxngSearchEngine("http://searxng:8080/").invoke("q"))

    assert result.success
    assert [item.url for item in result.data.results] == ["https://a.test"]
    assert result.data.results[0].snippet == "S"


def test_provider_outage_is_a_loud_failure(monkeypatch):
    _patch_client(monkeypatch, httpx.ConnectError("connection refused"))
    result = asyncio.run(SearxngSearchEngine("http://searxng:8080").invoke("q"))

    assert not result.success
    assert "searxng" in (result.message or "")


def test_bing_api_maps_web_pages(monkeypatch):
    payload = {
        "webPages": {
            "totalEstimatedMatches": 42,
            "value": [{"name": "T", "url": "https://b.test", "snippet": "S"}],
        }
    }
    _patch_client(monkeypatch, _StubResponse(payload))
    result = asyncio.run(BingApiSearchEngine("key").invoke("q", "past_day"))

    assert result.success
    assert result.data.total_results == 42
    assert result.data.results[0].url == "https://b.test"


def test_bing_html_empty_parse_is_a_failure_not_empty_success():
    result = _parse_bing_html("<html><body></body></html>", "q", None)

    assert not result.success
    assert "layout change" in (result.message or "")


def test_tool_registry_omits_search_tool_without_engine():
    class _Llm:
        model_name = "m"

    tools_without = ToolRegistry.build_default_tools(
        sandbox=object(),
        browser=object(),
        search_engine=None,
        llm=_Llm(),
        mcp_tool=_named_tool("mcp"),
        a2a_tool=_named_tool("a2a"),
    )
    names = [getattr(tool, "name", "") for tool in tools_without]
    assert "search" not in names


def _named_tool(name):
    from app.domain.services.tools.base import BaseTool

    class _Tool(BaseTool):
        pass

    tool = _Tool()
    tool.name = name
    return tool
