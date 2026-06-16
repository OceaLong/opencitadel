#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import re
import asyncio
from typing import Any, Dict, List
import httpx
from bs4 import BeautifulSoup

from app.application.services.marketplace.utils import (
    BLOCKED_VIDEO_KEYWORDS,
    build_platform_search_links,
    get_provider_meta,
    is_legal_video_url,
)

logger = logging.getLogger(__name__)

COPYRIGHT_NOTICE = "推荐正版资源，请支持版权"


class VideoSearchService:
    """聚合正版免费影视观看入口。"""

    def __init__(self) -> None:
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def search(self, query: str) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ValueError("请输入剧名")

        crawled: List[Dict[str, Any]] = []
        filtered_count = 0

        try:
            crawled, filtered_count = await self._crawl_bing_results(query)
        except Exception as exc:
            logger.warning("影视搜索爬取失败 query=%s: %s", query, exc)

        platform_links = build_platform_search_links(query)
        results: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        for item in crawled:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(item)

        for link in platform_links:
            url = link["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({
                "title": f"{query} - {link['platform']}站内搜索",
                "platform": link["platform"],
                "icon": link["icon"],
                "url": url,
                "quality": link["quality"],
                "condition": link["condition"],
                "trust_score": link["trust_score"],
                "source_type": "platform_search",
            })

        return {
            "query": query,
            "copyright_notice": COPYRIGHT_NOTICE,
            "results": results[:20],
            "stats": {
                "crawled_candidates": len(crawled) + filtered_count,
                "filtered_risk_sources": filtered_count,
                "legal_results": len(results),
            },
        }

    async def _crawl_bing_results(self, query: str) -> tuple[List[Dict[str, Any]], int]:
        search_query = f"{query} 免费观看 正版"
        params = {"q": search_query}

        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=10,
            follow_redirects=True,
        ) as client:
            response = await client.get("https://www.bing.com/search", params=params)
            response.raise_for_status()
            soup = await asyncio.to_thread(BeautifulSoup, response.text, "html.parser")

        legal_results: List[Dict[str, Any]] = []
        filtered_count = 0

        for item in soup.find_all("li", class_="b_algo"):
            title, url = self._extract_result(item)
            if not title or not url:
                continue

            lower_blob = f"{title} {url}".lower()
            if any(keyword in lower_blob for keyword in BLOCKED_VIDEO_KEYWORDS):
                filtered_count += 1
                continue

            if not is_legal_video_url(url):
                filtered_count += 1
                continue

            meta = get_provider_meta(url)
            legal_results.append({
                "title": title,
                "platform": meta["name"],
                "icon": meta["icon"],
                "url": url,
                "quality": "以平台页面为准",
                "condition": meta["condition"],
                "trust_score": 0.9,
                "source_type": "crawled",
            })

        return legal_results, filtered_count

    @staticmethod
    def _extract_result(item) -> tuple[str, str]:
        title, url = "", ""
        title_tag = item.find("h2")
        if title_tag:
            a_tag = title_tag.find("a")
            if a_tag:
                title = a_tag.get_text(strip=True)
                url = a_tag.get("href", "")
        if not title:
            for a_tag in item.find_all("a"):
                text = a_tag.get_text(strip=True)
                if len(text) > 8:
                    title = text
                    url = a_tag.get("href", "")
                    break
        url = re.sub(r"&amp;", "&", url or "")
        return title, url
