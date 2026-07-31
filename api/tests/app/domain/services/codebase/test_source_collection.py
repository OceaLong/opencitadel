#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.codebase import Codebase
from app.domain.models.tool_result import ToolResult
from app.domain.services.codebase.ingestion_runner import (
    CodebaseIngestionRunner,
    SourceCollectionResult,
)


class _CodebaseRepo:
    def __init__(self):
        self.codebase = Codebase(id="cb1")

    async def get_by_id(self, codebase_id, scope=None):
        return self.codebase if codebase_id == self.codebase.id else None


class _Uow:
    def __init__(self, repo):
        self.codebase = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Sandbox:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.batch_sizes: list[int] = []

    async def read_files(self, filepaths, *, max_length=512000):
        self.batch_sizes.append(len(filepaths))
        results = []
        for path in filepaths:
            content = self.files[path]
            results.append(
                ToolResult(
                    success=True,
                    data={"filepath": path, "content": content[:max_length]},
                )
            )
        return results


def _runner() -> CodebaseIngestionRunner:
    repo = _CodebaseRepo()
    return CodebaseIngestionRunner(
        uow_factory=lambda: _Uow(repo),
        sandbox_cls=MagicMock(),
        file_storage=MagicMock(),
    )


@pytest.mark.asyncio
async def test_ignored_files_do_not_consume_source_limit(monkeypatch):
    runner = _runner()
    workspace = "/home/ubuntu/codebase"
    ignored = [f"{workspace}/node_modules/p{i}.js" for i in range(6000)]
    ignored.append(f"{workspace}/src/vendor.min.js")
    source = f"{workspace}/src/main.py"
    sandbox = _Sandbox({source: "def main(): pass"})

    async def fake_exec_await(*args, **kwargs):
        return "\n".join([*ignored, source])

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    result = await runner._collect_source_files(
        sandbox,
        workspace,
        max_files=5000,
        batch_size=50,
    )

    assert isinstance(result, SourceCollectionResult)
    assert [entry.path for entry in result.entries] == ["src/main.py"]
    assert result.scanned == 6002
    assert result.skipped == 6001
    assert result.failed == 0
    assert result.truncated is False
    assert sandbox.batch_sizes == [1]


@pytest.mark.asyncio
async def test_collection_uses_bounded_batches(monkeypatch):
    runner = _runner()
    workspace = "/home/ubuntu/codebase"
    paths = [f"{workspace}/src/p{i}.py" for i in range(250)]
    sandbox = _Sandbox({path: f"print({i})" for i, path in enumerate(paths)})

    async def fake_exec_await(*args, **kwargs):
        return "\n".join(paths)

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    result = await runner._collect_source_files(
        sandbox,
        workspace,
        max_files=5000,
        batch_size=50,
    )

    assert len(result.entries) == 250
    assert sandbox.batch_sizes == [50, 50, 50, 50, 50]


@pytest.mark.asyncio
async def test_collection_reports_truncation_after_filtering(monkeypatch):
    runner = _runner()
    workspace = "/home/ubuntu/codebase"
    paths = [f"{workspace}/src/p{i}.py" for i in range(5)]
    sandbox = _Sandbox({path: f"print({i})" for i, path in enumerate(paths)})

    async def fake_exec_await(*args, **kwargs):
        return "\n".join(paths)

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    result = await runner._collect_source_files(
        sandbox,
        workspace,
        max_files=3,
        batch_size=2,
    )

    assert [entry.path for entry in result.entries] == [
        "src/p0.py",
        "src/p1.py",
        "src/p2.py",
    ]
    assert result.truncated is True
    assert sandbox.batch_sizes == [2, 1]


@pytest.mark.asyncio
async def test_collection_reports_failed_reads(monkeypatch):
    runner = _runner()
    workspace = "/home/ubuntu/codebase"
    ok_path = f"{workspace}/src/ok.py"
    bad_path = f"{workspace}/src/bad.py"

    class FailingSandbox(_Sandbox):
        async def read_files(self, filepaths, *, max_length=512000):
            self.batch_sizes.append(len(filepaths))
            return [
                ToolResult(
                    success=True,
                    data={"filepath": ok_path, "content": "print('ok')"},
                ),
                ToolResult(success=False, message="read failed"),
            ]

    async def fake_exec_await(*args, **kwargs):
        return "\n".join([ok_path, bad_path])

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    result = await runner._collect_source_files(
        FailingSandbox({}),
        workspace,
        max_files=5000,
        batch_size=50,
    )

    assert [entry.path for entry in result.entries] == ["src/ok.py"]
    assert result.failed == 1
    assert result.total_bytes == len("print('ok')".encode())
