"""Configurable web-search providers behind the ``SearchEngine`` port.

The legacy default scrapes Bing's HTML (``bing_search.py``) — fragile CSS
selectors, no API contract. These providers speak real APIs instead:

* ``searxng``  — self-hosted metasearch, no API key (``SEARCH_ENDPOINT``).
* ``tavily``   — hosted search API (``SEARCH_API_KEY``).
* ``bing_api`` — Bing Web Search v7 (``SEARCH_API_KEY`` + optional endpoint).

Every provider fails loudly (``ToolResult(success=False)``) instead of
returning a silent empty result set, so the agent surfaces the outage rather
than fabricating an answer from nothing.
"""

from __future__ import annotations

import logging

import httpx

from app.domain.external.search import SearchEngine
from app.domain.models.search import (
    SearchCapability,
    SearchProviderAvailability,
    SearchResultItem,
    SearchResults,
)
from app.domain.models.tool_result import ToolResult

logger = logging.getLogger(__name__)

# provider 枚举与可用性判定的单源（P2-11）：capability_service 与组合根
# 一律消费这里的判定，生产代码不得再对 provider 名做字符串特判。
_PROVIDER_AVAILABILITY: dict[str, SearchProviderAvailability] = {
    "none": SearchProviderAvailability.NOT_CONFIGURED,
    # HTML 抓取在目标改版/反爬时随时失效，标记 degraded 提示运维换 API 源。
    "bing_html": SearchProviderAvailability.DEGRADED,
    "searxng": SearchProviderAvailability.AVAILABLE,
    "tavily": SearchProviderAvailability.AVAILABLE,
    "bing_api": SearchProviderAvailability.AVAILABLE,
}


def available_providers() -> tuple[str, ...]:
    """All recognized SEARCH_PROVIDER values."""
    return tuple(sorted(_PROVIDER_AVAILABILITY))


def normalize_provider(provider: str | None) -> str:
    return (provider or "bing_html").strip().lower()


def resolve_search_capability(provider: str | None) -> SearchCapability:
    """Resolve one configured provider into its availability verdict."""
    normalized = normalize_provider(provider)
    availability = _PROVIDER_AVAILABILITY.get(normalized)
    if availability is None:
        raise ValueError(
            f"unknown SEARCH_PROVIDER '{provider}' (expected {'|'.join(available_providers())})"
        )
    return SearchCapability(provider=normalized, availability=availability)


_TIMEOUT = httpx.Timeout(15.0)

# date_range vocabulary comes from SearchTool's tool schema.
_SEARXNG_TIME_RANGE = {
    "past_day": "day",
    "past_week": "week",
    "past_month": "month",
    "past_year": "year",
}
_BING_FRESHNESS = {
    "past_day": "Day",
    "past_week": "Week",
    "past_month": "Month",
}


def _failure(provider: str, detail: str) -> ToolResult[SearchResults]:
    logger.warning("web search provider %s failed: %s", provider, detail)
    return ToolResult(
        success=False,
        message=f"web search via {provider} failed: {detail}",
        data=None,
    )


def _results(
    query: str,
    date_range: str | None,
    items: list[SearchResultItem],
    total: int | None = None,
) -> ToolResult[SearchResults]:
    return ToolResult(
        success=True,
        data=SearchResults(
            query=query,
            date_range=date_range,
            total_results=total if total is not None else len(items),
            results=items,
        ),
    )


class SearxngSearchEngine(SearchEngine):
    """Self-hosted SearXNG instance speaking its JSON API."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def invoke(self, query: str, date_range: str | None = None) -> ToolResult[SearchResults]:
        params: dict[str, str] = {"q": query, "format": "json"}
        time_range = _SEARXNG_TIME_RANGE.get(date_range or "")
        if time_range:
            params["time_range"] = time_range
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(f"{self._base_url}/search", params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _failure("searxng", str(exc))
        items = [
            SearchResultItem(
                title=str(entry.get("title") or ""),
                url=str(entry.get("url") or ""),
                snippet=str(entry.get("content") or ""),
            )
            for entry in payload.get("results") or []
            if entry.get("url")
        ]
        return _results(query, date_range, items)


class TavilySearchEngine(SearchEngine):
    """Hosted Tavily search API."""

    def __init__(self, api_key: str, *, endpoint: str = "https://api.tavily.com/search") -> None:
        self._api_key = api_key
        self._endpoint = endpoint

    async def invoke(self, query: str, date_range: str | None = None) -> ToolResult[SearchResults]:
        body = {"api_key": self._api_key, "query": query, "max_results": 10}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(self._endpoint, json=body)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _failure("tavily", str(exc))
        items = [
            SearchResultItem(
                title=str(entry.get("title") or ""),
                url=str(entry.get("url") or ""),
                snippet=str(entry.get("content") or ""),
            )
            for entry in payload.get("results") or []
            if entry.get("url")
        ]
        return _results(query, date_range, items)


class BingApiSearchEngine(SearchEngine):
    """Bing Web Search API v7 (key-based, unlike the HTML-scraping legacy)."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.bing.microsoft.com/v7.0/search",
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint

    async def invoke(self, query: str, date_range: str | None = None) -> ToolResult[SearchResults]:
        params: dict[str, str] = {"q": query, "count": "10"}
        freshness = _BING_FRESHNESS.get(date_range or "")
        if freshness:
            params["freshness"] = freshness
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(self._endpoint, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return _failure("bing_api", str(exc))
        web_pages = (payload.get("webPages") or {}).get("value") or []
        items = [
            SearchResultItem(
                title=str(entry.get("name") or ""),
                url=str(entry.get("url") or ""),
                snippet=str(entry.get("snippet") or ""),
            )
            for entry in web_pages
            if entry.get("url")
        ]
        total = (payload.get("webPages") or {}).get("totalEstimatedMatches")
        return _results(query, date_range, items, total=total)


def build_search_engine(
    provider: str,
    *,
    endpoint: str = "",
    api_key: str = "",
) -> SearchEngine | None:
    """Resolve SEARCH_PROVIDER into an engine; None disables the search tool.

    Misconfiguration fails fast at composition time rather than degrading into
    silent empty search results at runtime.
    """
    normalized = resolve_search_capability(provider).provider
    if normalized == "none":
        return None
    if normalized == "bing_html":
        # Deferred import: keeps the legacy scraper's bs4 dependency out of the
        # module import path for API-based deployments.
        from app.infrastructure.external.search.bing_search import BingSearchEngine

        return BingSearchEngine()
    if normalized == "searxng":
        if not endpoint:
            raise ValueError("SEARCH_PROVIDER=searxng requires SEARCH_ENDPOINT")
        return SearxngSearchEngine(endpoint)
    if normalized == "tavily":
        if not api_key:
            raise ValueError("SEARCH_PROVIDER=tavily requires SEARCH_API_KEY")
        return TavilySearchEngine(api_key)
    if not api_key:
        raise ValueError("SEARCH_PROVIDER=bing_api requires SEARCH_API_KEY")
    if endpoint:
        return BingApiSearchEngine(api_key, endpoint=endpoint)
    return BingApiSearchEngine(api_key)


__all__ = [
    "BingApiSearchEngine",
    "SearxngSearchEngine",
    "TavilySearchEngine",
    "available_providers",
    "build_search_engine",
    "normalize_provider",
    "resolve_search_capability",
]
