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


@dataclass
class WebDocument:
    title: str
    content: str
    mime: str = "text/markdown"


async def fetch_web_document(url: str, *, timeout_seconds: float = 20.0) -> WebDocument:
    current = validate_public_url(url)
    headers = {"User-Agent": "MyManus-KnowledgeBase/1.0"}
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
        response = None
        for _ in range(8):
            validate_public_url(current)
            response = await client.get(current, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise BadRequestError("URL 重定向缺少 Location 头")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            break
        else:
            raise BadRequestError("URL 重定向次数过多")
    if response is None:
        raise BadRequestError("无法获取网页内容")
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    title = _pick_title(soup) or current
    main = soup.find("main") or soup.find("article") or soup.body or soup
    content = md(str(main), heading_style="ATX").strip()
    return WebDocument(title=title, content=content, mime="text/markdown")


async def fetch_confluence_document(url: str, token: Optional[str] = None) -> WebDocument:
    validate_public_url(url)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title = _pick_title(soup) or url
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
