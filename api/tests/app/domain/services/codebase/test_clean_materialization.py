#!/usr/bin/env python
# -*- coding: utf-8 -*-
import io
import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.codebase import Codebase, CodebaseSourceType
from app.domain.models.tool_result import ToolResult
from app.domain.services.codebase.ingestion_runner import CodebaseIngestionRunner
from app.domain.services.codebase.static_analyzer import AnalysisResult


class _Repo:
    def __init__(self, codebase):
        self._codebase = codebase

    async def get_by_id(self, codebase_id, scope=None):
        return self._codebase if self._codebase.id == codebase_id else None

    async def save(self, codebase):
        self._codebase = codebase

    async def update_status(self, codebase_id, status, error=None):
        self._codebase.status = status
        self._codebase.error = error

    async def clear_analysis_data(self, codebase_id):
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


class _Uow:
    def __init__(self, repo):
        self.codebase = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Sandbox:
    id = "sandbox-1"

    def __init__(self):
        self.uploads: list[str] = []
        self.read_file = AsyncMock()

    async def upload_file(self, file_data, filepath, filename=None):
        self.uploads.append(filepath)
        return ToolResult(success=True)

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        return b"snapshot-bytes"


def _runner(codebase):
    repo = _Repo(codebase)
    sandbox = _Sandbox()
    sandbox_cls = MagicMock()
    sandbox_cls.create = AsyncMock(return_value=sandbox)
    sandbox_cls.get = AsyncMock(return_value=None)
    storage = MagicMock()

    async def download_file(file_id):
        return io.BytesIO(f"content:{file_id}".encode()), SimpleNamespace(
            id=file_id,
            filename=f"{file_id}.py",
        )

    storage.download_file = AsyncMock(side_effect=download_file)
    return (
        CodebaseIngestionRunner(
            uow_factory=lambda: _Uow(repo),
            sandbox_cls=sandbox_cls,
            file_storage=storage,
        ),
        repo,
        sandbox,
    )


@pytest.mark.asyncio
async def test_files_reanalysis_uses_unique_clean_workspace(monkeypatch):
    codebase = Codebase(
        id="cb1",
        source_type=CodebaseSourceType.FILES,
        source_ref=json.dumps({"file_ids": ["old", "keep"]}),
        ingest_task_id="build-1",
    )
    runner, _repo, sandbox = _runner(codebase)
    exec_calls: list[tuple[str, str, str]] = []

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        exec_calls.append((session_id, exec_dir, command))
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    _sandbox, first_workspace = await runner._materialize(codebase)
    codebase.ingest_task_id = "build-2"
    codebase.source_ref = json.dumps({"file_ids": ["keep"]})
    _sandbox, second_workspace = await runner._materialize(codebase)

    assert first_workspace.endswith("/codebase-builds/build-1")
    assert second_workspace.endswith("/codebase-builds/build-2")
    assert first_workspace != second_workspace
    assert any("rm -rf" in command and "mkdir -p" in command for _, _, command in exec_calls)
    assert f"{second_workspace}/old.py" not in sandbox.uploads
    assert f"{second_workspace}/keep.py" in sandbox.uploads


@pytest.mark.asyncio
async def test_git_clone_command_cannot_interpolate_shell(monkeypatch):
    codebase = Codebase(
        id="cb1",
        source_type=CodebaseSourceType.GIT,
        source_ref="https://example.com/repo.git;touch /tmp/pwned",
        ingest_task_id="build-1",
    )
    runner, _repo, _sandbox = _runner(codebase)
    exec_calls: list[str] = []

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout=120):
        exec_calls.append(command)
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    with pytest.raises(Exception):
        await runner._materialize(codebase)

    assert exec_calls == []


@pytest.mark.asyncio
async def test_run_persists_snapshot_before_analysis(monkeypatch):
    codebase = Codebase(
        id="cb1",
        name="demo",
        source_type=CodebaseSourceType.FILES,
        source_ref=json.dumps({"file_ids": ["main"]}),
        ingest_task_id="build-1",
    )
    runner, repo, sandbox = _runner(codebase)
    expected_digest = sha256(b"snapshot-bytes").hexdigest()
    expected_key = f"codebase-snapshots/sha256/{expected_digest}.tgz"

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._materialize",
        AsyncMock(return_value=(sandbox, "/home/ubuntu/codebase-builds/build-1")),
    )
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._collect_files",
        AsyncMock(return_value=[("main.py", "def main(): pass")]),
    )

    def analyze_tree(codebase_id, workspace, entries):
        assert repo._codebase.snapshot_key == expected_key
        return AnalysisResult(file_contents={"main.py": "def main(): pass"})

    runner._analyzer.analyze_tree = MagicMock(side_effect=analyze_tree)
    runner._indexer.build_chunks = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.ArtifactGenerator.generate_all",
        lambda *args, **kwargs: [],
    )

    events = []
    async for event in runner.run("cb1"):
        events.append(event)

    assert repo._codebase.snapshot_key == expected_key
