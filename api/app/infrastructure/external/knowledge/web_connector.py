"""SSRF-safe web and SaaS document connectors for knowledge ingestion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.errors import BadRequestError
from app.domain.external.web_document import WebDocument
from app.domain.models.knowledge_base import KBSourceType
from app.domain.runtime_policy import SourceAccessPolicy
from app.domain.services.knowledge_base.url_guard import validate_public_url
from app.domain.utils.time_utils import utc_now
from app.infrastructure.security.outbound_http import create_ssrf_safe_async_client

_MAX_WEB_DOCUMENT_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5


class HttpWebDocumentGateway:
    def __init__(self, *, policy_reader: OperationsPolicyReader) -> None:
        self._policy_reader = policy_reader

    async def fetch(self, source_type: KBSourceType, url: str) -> WebDocument:
        fetchers = {
            KBSourceType.WEB: fetch_web_document,
            KBSourceType.CONFLUENCE: fetch_confluence_document,
            KBSourceType.FEISHU: fetch_feishu_document,
        }
        fetcher = fetchers.get(source_type)
        if fetcher is None:
            raise BadRequestError("URL 只能使用 web、confluence 或 feishu 来源类型")
        return await fetcher(url, policy_provider=self._source_access_policy)

    async def _source_access_policy(self) -> SourceAccessPolicy:
        active = await self._policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        return active.revision.policy.source_access


SourceAccessPolicyProvider = Callable[[], Awaitable[SourceAccessPolicy]]


async def fetch_web_document(
    url: str,
    *,
    policy_provider: SourceAccessPolicyProvider,
    timeout_seconds: float = 20.0,
) -> WebDocument:
    from markdownify import markdownify as md

    headers = {"User-Agent": "OpenCitadel-KnowledgeBase/1.0"}
    response, current = await _request_with_safe_redirects(
        url,
        policy_provider=policy_provider,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    title = _pick_title(soup) or current
    main = soup.find("main") or soup.find("article") or soup.body or soup
    content = md(str(main), heading_style="ATX").strip()
    return WebDocument(title=title, content=content)


async def fetch_confluence_document(
    url: str,
    *,
    policy_provider: SourceAccessPolicyProvider,
    token: str | None = None,
) -> WebDocument:
    from markdownify import markdownify as md

    headers = {"Authorization": f"Bearer {token}"} if token else None
    response, current = await _request_with_safe_redirects(
        url,
        policy_provider=policy_provider,
        headers=headers,
        timeout_seconds=20.0,
    )
    soup = BeautifulSoup(response.text, "html.parser")
    title = _pick_title(soup) or current
    content = md(
        str(soup.find("main") or soup.body or soup),
        heading_style="ATX",
    ).strip()
    return WebDocument(title=title, content=content)


async def fetch_feishu_document(
    url: str,
    *,
    policy_provider: SourceAccessPolicyProvider,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> WebDocument:
    _ = (app_id, app_secret)
    return await fetch_web_document(url, policy_provider=policy_provider)


async def _request_with_safe_redirects(
    url: str,
    *,
    policy_provider: SourceAccessPolicyProvider,
    headers: dict[str, str] | None,
    timeout_seconds: float,
) -> tuple[httpx.Response, str]:
    current = validate_public_url(url, policy=await policy_provider())
    async with create_ssrf_safe_async_client(
        timeout=timeout_seconds,
        follow_redirects=False,
        allowed_ports={80, 443},
    ) as client:
        response = None
        for _ in range(_MAX_REDIRECTS + 1):
            response = await client.get(current, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise BadRequestError("URL 重定向缺少 Location 头")
                current = urljoin(current, location)
                current = validate_public_url(
                    current,
                    policy=await policy_provider(),
                )
                continue
            response.raise_for_status()
            _ensure_bounded_response(response)
            break
        else:
            raise BadRequestError("URL 重定向次数过多")
    if response is None:
        raise BadRequestError("无法获取网页内容")
    return response, current


def _pick_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


def _ensure_bounded_response(response: httpx.Response) -> None:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > _MAX_WEB_DOCUMENT_BYTES:
            raise BadRequestError("网页内容超过允许大小")
    if len(response.content) > _MAX_WEB_DOCUMENT_BYTES:
        raise BadRequestError("网页内容超过允许大小")


__all__ = [
    "HttpWebDocumentGateway",
    "WebDocument",
    "fetch_confluence_document",
    "fetch_feishu_document",
    "fetch_web_document",
]
