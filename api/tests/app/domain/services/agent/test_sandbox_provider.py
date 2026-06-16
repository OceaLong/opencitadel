#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from app.domain.external.browser import Browser
from app.domain.models.tool_result import ToolResult
from app.domain.services.agent.sandbox_provider import LazyBrowser, LazySandbox, SandboxProvider


class _FakeSandbox:
    def __init__(self, sandbox_id: str = "fake-sandbox-abc") -> None:
        self._id = sandbox_id

    @property
    def id(self) -> str:
        return self._id

    @property
    def vnc_url(self) -> str:
        return "ws://127.0.0.1:5901"

    @property
    def cdp_url(self) -> str:
        return "http://127.0.0.1:9222"

    async def read_file(self, filepath: str, **kwargs) -> ToolResult:
        return ToolResult(success=True, data={"content": "hello", "filepath": filepath})

    async def get_browser(self, supports_multimodal: bool = False) -> Browser:
        browser = MagicMock(spec=Browser)
        browser.view_page = AsyncMock(return_value=ToolResult(success=True))
        browser.cleanup = AsyncMock()
        return browser


class _FakeSandboxCls:
    create_calls = 0
    get_calls = 0

    @classmethod
    def reset(cls) -> None:
        cls.create_calls = 0
        cls.get_calls = 0

    @classmethod
    async def get(cls, sandbox_id: str) -> Optional[_FakeSandbox]:
        cls.get_calls += 1
        return None

    @classmethod
    async def create(cls) -> _FakeSandbox:
        cls.create_calls += 1
        return _FakeSandbox()


def _uow_factory():
    session = MagicMock()
    session.sandbox_id = None

    uow = MagicMock()
    uow.session.get_by_id = AsyncMock(return_value=session)
    uow.session.save = AsyncMock()

    class _Ctx:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, *args):
            return False

    return lambda: _Ctx()


def test_provider_does_not_create_until_get():
    _FakeSandboxCls.reset()
    provider = SandboxProvider(
        session_id="sess-1",
        sandbox_id=None,
        sandbox_cls=_FakeSandboxCls,
        uow_factory=_uow_factory(),
    )
    assert provider.materialized() is None
    assert _FakeSandboxCls.create_calls == 0


def test_lazy_sandbox_creates_on_first_tool_call():
    _FakeSandboxCls.reset()
    provider = SandboxProvider(
        session_id="sess-1",
        sandbox_id=None,
        sandbox_cls=_FakeSandboxCls,
        uow_factory=_uow_factory(),
    )
    lazy = LazySandbox(provider)

    async def _run():
        result = await lazy.read_file("/tmp/test.txt")
        assert result.success
        assert _FakeSandboxCls.create_calls == 1
        assert provider.materialized() is not None
        assert lazy.id == "fake-sandbox-abc"

    asyncio.run(_run())


def test_concurrent_get_creates_once():
    _FakeSandboxCls.reset()
    provider = SandboxProvider(
        session_id="sess-1",
        sandbox_id=None,
        sandbox_cls=_FakeSandboxCls,
        uow_factory=_uow_factory(),
    )
    lazy = LazySandbox(provider)

    async def _run():
        await asyncio.gather(
            lazy.read_file("/a"),
            lazy.read_file("/b"),
            lazy.read_file("/c"),
        )
        assert _FakeSandboxCls.create_calls == 1

    asyncio.run(_run())


def test_lazy_browser_creates_sandbox_on_first_use():
    _FakeSandboxCls.reset()
    provider = SandboxProvider(
        session_id="sess-1",
        sandbox_id=None,
        sandbox_cls=_FakeSandboxCls,
        uow_factory=_uow_factory(),
    )
    browser = LazyBrowser(provider, supports_multimodal=False)

    async def _run():
        await browser.view_page()
        assert _FakeSandboxCls.create_calls == 1

    asyncio.run(_run())


def test_lazy_browser_cleanup_noop_when_not_materialized():
    _FakeSandboxCls.reset()
    provider = SandboxProvider(
        session_id="sess-1",
        sandbox_id=None,
        sandbox_cls=_FakeSandboxCls,
        uow_factory=_uow_factory(),
    )
    browser = LazyBrowser(provider)

    async def _run():
        await browser.cleanup()
        assert _FakeSandboxCls.create_calls == 0

    asyncio.run(_run())


def test_provider_reuses_existing_sandbox_id():
    existing = _FakeSandbox("existing-id")

    class _ReuseCls:
        @classmethod
        async def get(cls, sandbox_id: str):
            if sandbox_id == "existing-id":
                return existing
            return None

        @classmethod
        async def create(cls):
            raise AssertionError("should not create when get succeeds")

    provider = SandboxProvider(
        session_id="sess-1",
        sandbox_id="existing-id",
        sandbox_cls=_ReuseCls,
        uow_factory=_uow_factory(),
    )

    async def _run():
        sandbox = await provider.get()
        assert sandbox.id == "existing-id"

    asyncio.run(_run())
