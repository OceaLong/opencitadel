import io
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.codebase import Codebase, CodebaseSourceType, CodebaseStatus
from app.domain.models.tool_result import ToolResult
from app.domain.runtime_policy import CodebaseAnalysisPolicy, CodebaseExecutionPolicy
from app.domain.services.codebase.ingestion_runner import CodebaseIngestionRunner
from app.domain.services.codebase.snapshot_service import VersionedCodeSource


class _FakeCodebaseRepo:
    def __init__(self, codebase: Codebase):
        self._codebase = codebase
        self.status_updates: list[tuple[str, CodebaseStatus, str | None]] = []

    async def get_by_id(self, codebase_id: str, scope=None):
        return self._codebase if self._codebase.id == codebase_id else None

    async def save(self, codebase: Codebase):
        self._codebase = codebase

    async def update_status(
        self, codebase_id: str, status: CodebaseStatus, error: str | None = None
    ):
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

    async def create_workspace_snapshot(self, snapshot_id: str) -> bytes:
        return b"snapshot-bytes"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_runner(codebase: Codebase) -> tuple[CodebaseIngestionRunner, _FakeCodebaseRepo]:
    repo = _FakeCodebaseRepo(codebase)
    object_storage = MagicMock()
    object_storage.put_bytes = AsyncMock()
    runner = CodebaseIngestionRunner(
        uow_factory=lambda: _FakeUow(repo),
        sandbox_factory=MagicMock(),
        file_storage=MagicMock(),
        object_storage=object_storage,
    )
    return runner, repo


@pytest.mark.anyio
async def test_collect_files_parses_shell_dict_output(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT)
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout_seconds=120):
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
    entries = await runner._collect_files(
        sandbox,
        workspace,
        policy=CodebaseAnalysisPolicy(),
    )

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

    entries = await runner._collect_files(
        sandbox,
        "/home/ubuntu/codebase",
        policy=CodebaseAnalysisPolicy(),
    )

    assert entries == []


@pytest.mark.anyio
async def test_collect_files_skips_ignored_extensions(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT)
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout_seconds=120):
        return f"{workspace}/foo.py\n{workspace}/logo.png\n"

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )
    sandbox.read_file.return_value = ToolResult(
        success=True,
        data={"filepath": f"{workspace}/foo.py", "content": "print('hi')"},
    )

    entries = await runner._collect_files(
        sandbox,
        workspace,
        policy=CodebaseAnalysisPolicy(),
    )

    assert entries == [("foo.py", "print('hi')")]
    sandbox.read_file.assert_awaited_once()


@pytest.mark.anyio
async def test_collect_files_logs_failed_read(monkeypatch):
    codebase = Codebase(id="cb1", source_type=CodebaseSourceType.GIT)
    runner, _ = _make_runner(codebase)
    sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase"

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout_seconds=120):
        return f"{workspace}/foo.py\n"

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )
    sandbox.read_file.return_value = ToolResult(success=False, message="读取文件失败")

    entries = await runner._collect_files(
        sandbox,
        workspace,
        policy=CodebaseAnalysisPolicy(),
    )

    assert entries == []


@pytest.mark.anyio
async def test_materialize_zip_uses_python_zipfile(monkeypatch):
    codebase = Codebase(
        id="cb1",
        owner_user_id="user-1",
        source_type=CodebaseSourceType.ZIP,
        source_ref='{"file_id": "file-1"}',
    )
    runner, _ = _make_runner(codebase)
    fake_sandbox = _FakeSandbox()
    exec_calls: list[tuple[str, str, str]] = []

    async def fake_create(*, owner_scope):
        assert owner_scope.user_id == "user-1"
        return fake_sandbox

    runner._sandbox_factory.create = fake_create
    runner._sandbox_factory.get = AsyncMock(return_value=None)
    zip_stream = io.BytesIO()
    with zipfile.ZipFile(zip_stream, "w") as archive:
        archive.writestr("main.py", "print('hi')")
    runner._file_storage.download_file = AsyncMock(
        return_value=(MagicMock(read=lambda: zip_stream.getvalue()), MagicMock(filename="repo.zip"))
    )
    fake_sandbox.upload_file = AsyncMock()

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout_seconds=120):
        exec_calls.append((session_id, exec_dir, command))
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    await runner._materialize(codebase, build_identity="candidate-1")

    assert any("python3 -m zipfile -e upload.zip" in command for _, _, command in exec_calls)
    assert not any("unzip -o upload.zip" in command for _, _, command in exec_calls)


@pytest.mark.anyio
async def test_materialize_mkdir_uses_sandbox_home(monkeypatch):
    codebase = Codebase(
        id="cb1",
        owner_user_id="user-1",
        source_type=CodebaseSourceType.GIT,
        source_ref="https://example.com/repo.git",
    )
    runner, _ = _make_runner(codebase)
    fake_sandbox = _FakeSandbox()
    workspace = "/home/ubuntu/codebase-builds/build-1"
    exec_calls: list[tuple[str, str, str]] = []

    async def fake_create(*, owner_scope):
        assert owner_scope.user_id == "user-1"
        return fake_sandbox

    runner._sandbox_factory.create = fake_create
    runner._sandbox_factory.get = AsyncMock(return_value=None)

    async def fake_exec_await(sb, session_id, exec_dir, command, *, timeout_seconds=120):
        exec_calls.append((session_id, exec_dir, command))
        return ""

    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.exec_command_await",
        fake_exec_await,
    )

    sandbox, result_workspace = await runner._materialize(
        codebase,
        build_identity="build-1",
    )

    assert sandbox is fake_sandbox
    assert result_workspace == workspace
    assert exec_calls[0] == (
        "ingest",
        "/home/ubuntu",
        f"rm -rf {workspace} && mkdir -p {workspace}",
    )


@pytest.mark.anyio
async def test_run_build_closes_candidate_on_unexpected_pipeline_exception(monkeypatch):
    runner, _ = _make_runner(Codebase(id="cb1"))
    failure = LookupError("repository write failed")
    monkeypatch.setattr(runner, "_load_build_context", AsyncMock(side_effect=failure))
    fail_candidate = AsyncMock()
    monkeypatch.setattr(runner, "_fail_candidate", fail_candidate)

    events = [
        event
        async for event in runner.run_build(
            "build-1",
            policy=CodebaseExecutionPolicy(),
        )
    ]

    fail_candidate.assert_awaited_once_with("build-1", "repository write failed")
    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].message == "repository write failed"


@pytest.mark.anyio
async def test_candidate_snapshot_is_stored_before_its_key_can_be_published():
    runner, _ = _make_runner(Codebase(id="cb1"))
    source_tree = {"src/index.ts": "export const beacon = true;\n"}

    snapshot = await runner._create_and_store_snapshot("version-1", source_tree)

    runner._object_storage.put_bytes.assert_awaited_once_with(
        snapshot.snapshot_key,
        snapshot.snapshot_bytes,
    )
    assert (
        VersionedCodeSource._read_member(snapshot.snapshot_bytes, "src/index.ts")
        == "export const beacon = true;\n"
    )
