#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lightweight web and SaaS document connectors for KB ingestion."""
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from app.application.errors.exceptions import BadRequestError
from app.domain.services.knowledge_base.url_guard import validate_public_url
from app.infrastructure.security.outbound_http import (
    create_ssrf_safe_async_client,
)

_MAX_WEB_DOCUMENT_BYTES = 5 * 1024 * 1024
_MAX_REDIRECTS = 5


@dataclass
class WebDocument:
    title: str
    content: str
    mime: str = "text/markdown"


async def fetch_web_document(url: str, *, timeout_seconds: float = 20.0) -> WebDocument:
    headers = {"User-Agent": "OpenCitadel-KnowledgeBase/1.0"}
    response, current = await _request_with_safe_redirects(
        url,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    title = _pick_title(soup) or current
    main = soup.find("main") or soup.find("article") or soup.body or soup
    content = md(str(main), heading_style="ATX").strip()
    return WebDocument(title=title, content=content, mime="text/markdown")


async def _request_with_safe_redirects(
    url: str,
    *,
    headers: dict[str, str] | None,
    timeout_seconds: float,
) -> tuple[httpx.Response, str]:
    current = validate_public_url(url)
    async with create_ssrf_safe_async_client(
        timeout=timeout_seconds,
        follow_redirects=False,
        allowed_ports={80, 443},
    ) as client:
        response = None
        for _ in range(_MAX_REDIRECTS + 1):
            validate_public_url(current)
            response = await client.get(current, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise BadRequestError("URL 重定向缺少 Location 头")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            _ensure_bounded_response(response)
            break
        else:
            raise BadRequestError("URL 重定向次数过多")
    if response is None:
        raise BadRequestError("无法获取网页内容")
    return response, current


async def fetch_confluence_document(url: str, token: Optional[str] = None) -> WebDocument:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    response, current = await _request_with_safe_redirects(
        url,
        headers=headers,
        timeout_seconds=20.0,
    )
    soup = BeautifulSoup(response.text, "html.parser")
    title = _pick_title(soup) or current
    content = md(str(soup.find("main") or soup.body or soup), heading_style="ATX").strip()
    return WebDocument(title=title, content=content, mime="text/markdown")


async def fetch_feishu_document(url: str, app_id: Optional[str] = None, app_secret: Optional[str] = None) -> WebDocument:
    _ = (app_id, app_secret)
    return await fetch_web_document(url)


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
