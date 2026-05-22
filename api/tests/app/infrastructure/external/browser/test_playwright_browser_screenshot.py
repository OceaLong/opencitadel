#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser


async def _test_view_page_always_returns_text_content():
    browser = PlaywrightBrowser("http://127.0.0.1:9222", supports_multimodal=True)
    browser._ensure_page = AsyncMock()
    browser.wait_for_page_load = AsyncMock()
    browser._extract_interactive_elements = AsyncMock(return_value=["0:<button>Go</button>"])
    browser._extract_content = AsyncMock(return_value="# Title")

    result = await browser.view_page()
    assert result.success is True
    assert result.data["content"] == "# Title"
    assert "screenshot_base64" not in result.data


def test_view_page_always_returns_text_content():
    asyncio.run(_test_view_page_always_returns_text_content())


async def _test_take_screenshot_returns_base64_when_multimodal():
    browser = PlaywrightBrowser("http://127.0.0.1:9222", supports_multimodal=True)
    with patch.object(browser, "_ensure_page", AsyncMock()), patch.object(
            browser, "wait_for_page_load", AsyncMock()
    ), patch.object(browser, "screenshot", AsyncMock(return_value=b"png-bytes")), patch.object(
            browser, "_extract_interactive_elements", AsyncMock(return_value=[])
    ):
        result = await browser.take_screenshot()
    assert result.success is True
    assert result.data["screenshot_base64"]


def test_take_screenshot_returns_base64_when_multimodal():
    asyncio.run(_test_take_screenshot_returns_base64_when_multimodal())


async def _test_take_screenshot_rejects_non_multimodal():
    browser = PlaywrightBrowser("http://127.0.0.1:9222", supports_multimodal=False)
    result = await browser.take_screenshot()
    assert result.success is False


def test_take_screenshot_rejects_non_multimodal():
    asyncio.run(_test_take_screenshot_rejects_non_multimodal())
