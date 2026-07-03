#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.models.tool_result import ToolResult
from app.infrastructure.external.browser.playwright_browser import (
    PlaywrightBrowser,
    _is_navigation_context_error,
)


def test_is_navigation_context_error():
    assert _is_navigation_context_error(
        Exception("Page.evaluate: Execution context was destroyed, most likely because of a navigation")
    )
    assert not _is_navigation_context_error(Exception("element not found"))


async def _test_build_action_verification_note_survives_navigation_error():
    browser = PlaywrightBrowser("http://127.0.0.1:9222")
    browser._ensure_page = AsyncMock()
    browser._wait_for_stable_page = AsyncMock()
    browser._current_page_url = MagicMock(return_value="https://example.com/page")

    call_count = 0

    async def flaky_count():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Execution context was destroyed, most likely because of a navigation")
        return 5

    browser._interactive_element_count = flaky_count

    note = await browser._build_action_verification_note(
        before_url="https://example.com/old",
        before_element_count=3,
        action="点击",
    )
    assert "点击已执行" in note
    assert "可交互元素: 3 -> 5" in note
    assert browser._wait_for_stable_page.await_count >= 1
    browser._wait_for_stable_page.assert_any_await("https://example.com/old")


def test_build_action_verification_note_survives_navigation_error():
    asyncio.run(_test_build_action_verification_note_survives_navigation_error())


async def _test_build_action_verification_note_degrades_when_count_unavailable():
    browser = PlaywrightBrowser("http://127.0.0.1:9222")
    browser._ensure_page = AsyncMock()
    browser._wait_for_stable_page = AsyncMock()
    browser._current_page_url = MagicMock(return_value="https://example.com/same")
    browser._interactive_element_count = AsyncMock(
        side_effect=Exception("Execution context was destroyed")
    )

    note = await browser._build_action_verification_note(
        before_url="https://example.com/same",
        before_element_count=2,
        action="点击",
    )
    assert "点击已执行" in note
    assert "页面验证已跳过" in note


def test_build_action_verification_note_degrades_when_count_unavailable():
    asyncio.run(_test_build_action_verification_note_degrades_when_count_unavailable())


async def _test_click_returns_success_after_navigation():
    browser = PlaywrightBrowser("http://127.0.0.1:9222")
    browser._ensure_page = AsyncMock()
    browser._interactive_element_count = AsyncMock(return_value=1)
    browser.page = MagicMock()
    browser.page.mouse = MagicMock()
    browser.page.mouse.click = AsyncMock()
    browser._build_action_verification_note = AsyncMock(
        return_value="点击已执行; URL: old -> new"
    )

    result = await browser.click(coordinate_x=10.0, coordinate_y=20.0)
    assert result.success is True
    assert "点击已执行" in result.message
    browser._build_action_verification_note.assert_awaited_once()


def test_click_returns_success_after_navigation():
    asyncio.run(_test_click_returns_success_after_navigation())
