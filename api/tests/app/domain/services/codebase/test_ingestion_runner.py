#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.codebase import Codebase, CodebaseSourceType, CodebaseStatus
from app.domain.models.event import ErrorEvent
from app.domain.models.tool_result import ToolResult
from app.domain.services.codebase.ingestion_runner import CodebaseIngestionRunner


class _FakeCodebaseRepo:
    def __init__(self, codebase: Codebase):
        self._codebase = codebase
        self.status_updates: list[tuple[str, CodebaseStatus, str | None]] = []

    async def get_by_id(self, codebase_id: str, scope=None):
        return self._codebase if self._codebase.id == codebase_id else None

    async def save(self, codebase: Codebase):
        self._codebase = codebase

    async def update_status(self, codebase_id: str, status: CodebaseStatus, error: str | None = None):
        self.status_updates.append((codebase_id, status, error))
        self._codebase.status = status
        self._codebase.error = error

    async def clear_analysis_data(self, codebase_id: str):
        return None

    async def save_files(self, files):
        return None

    async def save_symbols(self, symbols):
        return None

    async def flush(self):
        return None

    async def save_edges(self, edges):
        return None

    async def save_chunks(self, chunks):
        return None

    async def save_artifacts(self, artifacts):
        return None


class _FakeUow:
    def __init__(self, repo: _FakeCodebaseRepo):
        self.codebase = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSandbox:
    id = "sandbox-1"

    def __init__(self):
        self.read_file = AsyncMock()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_runner(codebase: Codebase) -> tuple[CodebaseIngestionRunner, _FakeCodebaseRepo]:
    repo = _FakeCodebaseRepo(codebase)
    runner = CodebaseIngestionRunner(
        uow_factory=lambda: _FakeUow(repo),
        sandbox_cls=MagicMock(),
        file_storage=MagicMock(),
    )
    return runner, repo


@pytest.mark.anyio
async def test_collect_files_parses_shell_dict_output(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT)
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        assert sb is sandbox
        return f"{workspace}/foo.py\n"

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )
    sandbox.read_file.return_value = ToolResult(
        success=True,
        data={"filepath": f"{workspace}/foo.py", "content": "print('hi')"},
    )

    entries = await runner._collect_files(sandbox, workspace)

    assert entries == [("foo.py", "print('hi')")]


@pytest.mark.anyio
async def test_collect_files_empty_on_failed_exec(monkeypatch):
    codebase = Codebase(id="cb1")
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()

    async def fake_exec_raises(*args, **kwargs):
        raise RuntimeError("find failed")

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_raises,
    )

    entries = await runner._collect_files(sandbox, "/home/ubuntu/codebase")

    assert entries == []


@pytest.mark.anyio
async def test_collect_files_skips_ignored_extensions(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT)
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        return f"{workspace}/foo.py\n{workspace}/logo.png\n"

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )
    sandbox.read_file.return_value = ToolResult(
        success=True,
        data={"filepath": f"{workspace}/foo.py", "content": "print('hi')"},
    )

    entries = await runner._collect_files(sandbox, workspace)

    assert entries == [("foo.py", "print('hi')")]
    sandbox.read_file.assert_awaited_once()


@pytest.mark.anyio
async def test_collect_files_logs_failed_read(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT)
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        return f"{workspace}/foo.py\n"

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )
    sandbox.read_file.return_value = ToolResult(success=False, message="读取文件失败")

    entries = await runner._collect_files(sandbox, workspace)

    assert entries == []


@pytest.mark.anyio
async def test_materialize_zip_uses_python_zipfile(monkeypatch):
    codebase = Codebase(
        id="cb1",
        source_type=CodebaseSourceType.ZIP,
        source_ref='{"file_id": "file-1"}',
    )
    runner, _ = _make_runner(codebase)
    fake_sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"
    exec_calls: list[tuple[str, str, str]] = []

    async def fake_create():
        return fake_sandbox

    runner._sandbox_cls.create = fake_create
    runner._sandbox_cls.get = AsyncMock(return_value=None)
    runner._file_storage.download_file = AsyncMock(
        return_value=(MagicMock(read=lambda: b"zip-bytes"), MagicMock(filename="repo.zip"))
    )
    fake_sandbox.upload_file = AsyncMock()

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        exec_calls.append((session_id, exec_dir, command))
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    await runner._materialize(codebase)

    assert any(
        "python3 -m zipfile -e upload.zip" in command
        for _, _, command in exec_calls
    )
    assert not any("unzip -o upload.zip" in command for _, _, command in exec_calls)


@pytest.mark.anyio
async def test_run_flushes_symbols_before_edges(monkeypatch):
    from app.domain.services.codebase.static_analyzer import AnalysisResult
    from app.domain.models.codebase import CodebaseFile, CodebaseSymbol, CodebaseEdge, SymbolKind, EdgeKind

    codebase = Codebase(
        id="cb1",
        source_type=CodebaseSourceType.GIT,
        source_ref="https://example.com/repo.git",
    )
    repo = _FakeCodebaseRepo(codebase)
    call_order: list[str] = []

    async def track_save_files(files):
        call_order.append("save_files")

    async def track_save_symbols(symbols):
        call_order.append("save_symbols")

    async def track_flush():
        call_order.append("flush")

    async def track_save_edges(edges):
        call_order.append("save_edges")

    repo.save_files = track_save_files
    repo.save_symbols = track_save_symbols
    repo.flush = track_flush
    repo.save_edges = track_save_edges

    runner = CodebaseIngestionRunner(
        uow_factory=lambda: _FakeUow(repo),
        sandbox_cls=MagicMock(),
        file_storage=MagicMock(),
    )
    fake_sandbox = _FakeSandbox()

    async def fake_create():
        return fake_sandbox

    runner._sandbox_cls.create = fake_create
    runner._sandbox_cls.get = AsyncMock(return_value=None)

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._collect_files",
        AsyncMock(return_value=[("main.py", "def main(): pass")]),
    )

    analysis = AnalysisResult(
        files=[
            CodebaseFile(id="f1", codebase_id="cb1", path="main.py", language="python", size=10, sha="abc"),
        ],
        symbols=[
            CodebaseSymbol(
                id="s1",
                codebase_id="cb1",
                file_id="f1",
                name="main",
                kind=SymbolKind.FUNCTION,
                signature="def main()",
                start_line=1,
                end_line=1,
            ),
        ],
        edges=[
            CodebaseEdge(
                id="e1",
                codebase_id="cb1",
                src_symbol_id="s1",
                dst_symbol_id=None,
                callee_name="print",
                kind=EdgeKind.CALL,
            ),
        ],
        file_contents={"main.py": "def main(): pass"},
    )
    runner._analyzer.analyze_tree = MagicMock(return_value=analysis)
    runner._indexer.build_chunks = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.ArtifactGenerator.generate_all",
        lambda *args, **kwargs: [],
    )

    events = []
    async for event in runner.run("cb1"):
        events.append(event)

    assert call_order == ["save_files", "save_symbols", "flush", "save_edges"]


@pytest.mark.anyio
async def test_materialize_mkdir_uses_sandbox_home(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT, source_ref="https://example.com/repo.git")
    runner, _ = _make_runner(codebase)
    fake_sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"
    exec_calls: list[tuple[str, str, str]] = []

    async def fake_create():
        return fake_sandbox

    runner._sandbox_cls.create = fake_create
    runner._sandbox_cls.get = AsyncMock(return_value=None)

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        exec_calls.append((session_id, exec_dir, command))
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    sandbox, result_workspace = await runner._materialize(codebase)

    assert sandbox is fake_sandbox
    assert result_workspace == workspace
    assert exec_calls[0] == ("ingest", "/home/ubuntu", f"mkdir -p {workspace}")


@pytest.mark.anyio
async def test_run_materialize_failure_yields_error_and_reraises(monkeypatch):
    codebase = Codebase(
        id="cb1",
        source_type=CodebaseSourceType.GIT,
        source_ref="https://example.com/repo.git",
    )
    runner, repo = _make_runner(codebase)
    fake_sandbox = _FakeSandbox()

    async def fake_create():
        return fake_sandbox

    runner._sandbox_cls.create = fake_create
    runner._sandbox_cls.get = AsyncMock(return_value=None)

    call_count = 0

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ""
        raise RuntimeError("git clone failed")

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    events = []
    with pytest.raises(RuntimeError, match="git clone failed"):
        async for event in runner.run("cb1"):
            events.append(event)

    assert any(isinstance(e, ErrorEvent) for e in events)
    assert events[-1].error == "git clone failed"
    assert codebase.status == CodebaseStatus.FAILED
    assert any(status == CodebaseStatus.FAILED for _, status, _ in repo.status_updates)
