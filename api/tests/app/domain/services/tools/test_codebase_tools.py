from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.tool_result import ToolResult
from app.domain.runtime_policy import (
    CodebaseRetrievalPolicy,
    CodebaseRetrievalRunPolicy,
)
from app.domain.services.codebase.hybrid_retriever import (
    CodeSearchResponse,
    CodeSearchResult,
)
from app.domain.services.codebase.snapshot_service import (
    CodeSnapshotService,
    CodeSourceProvenance,
    VersionedCodeSource,
)
from app.domain.services.tools.codebase_tools import CodebaseTool

_CODE_POLICY = CodebaseRetrievalRunPolicy(
    vector_enabled=True,
    retrieval=CodebaseRetrievalPolicy(),
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_read_code_joins_workspace_path():
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock(
        return_value=ToolResult(success=True, data={"content": "print('hi')"}),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=sandbox,
        policy=_CODE_POLICY,
        workspace_path="/home/ubuntu/codebase",
    )

    result = await tool.read_code("src/main.py")

    sandbox.read_file.assert_awaited_once_with(
        "/home/ubuntu/codebase/src/main.py",
        start_line=None,
        end_line=None,
    )
    assert "src/main.py" in result
    assert "print('hi')" in result


@pytest.mark.anyio
async def test_read_code_returns_error_message_on_failure():
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock(
        return_value=ToolResult(success=False, message="not found"),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=sandbox,
        policy=_CODE_POLICY,
        workspace_path="/home/ubuntu/codebase",
    )

    result = await tool.read_code("missing.py")

    assert "读取失败" in result
    assert "not found" in result


class _ObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]


@pytest.mark.anyio
async def test_read_prefers_published_version_source_reader_over_sandbox():
    materialized = await CodeSnapshotService().create(
        "cbv1",
        {"src/main.py": "first\npublished\nthird\n"},
    )
    source_reader = VersionedCodeSource(
        version_id="cbv1",
        snapshot_key=materialized.snapshot_key,
        source_digest=materialized.source_digest,
        object_storage=_ObjectStorage({materialized.snapshot_key: materialized.snapshot_bytes}),
    )
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock()
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=sandbox,
        policy=_CODE_POLICY,
        source_reader=source_reader,
        version_id="cbv1",
    )

    result = await tool.read("src/main.py", start_line=2, end_line=2)

    assert result.content == "published\n"
    assert result.path == "src/main.py"
    assert result.provenance is CodeSourceProvenance.PUBLISHED_VERSION
    assert result.base_version_id == "cbv1"
    sandbox.read_file.assert_not_awaited()


@pytest.mark.anyio
async def test_read_labels_session_workspace_as_mutable_overlay():
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock(
        return_value=ToolResult(success=True, data={"content": "local edit\n"}),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=sandbox,
        policy=_CODE_POLICY,
        workspace_path="/workspace",
        base_version_id="cbv1",
    )

    result = await tool.read("src/main.py")

    sandbox.read_file.assert_awaited_once_with(
        "/workspace/src/main.py",
        start_line=None,
        end_line=None,
    )
    assert result.content == "local edit\n"
    assert result.provenance is CodeSourceProvenance.SESSION_WORKSPACE
    assert result.base_version_id == "cbv1"


@pytest.mark.anyio
async def test_read_code_includes_source_provenance_label():
    materialized = await CodeSnapshotService().create(
        "cbv1",
        {"src/main.py": "print('published')\n"},
    )
    source_reader = VersionedCodeSource(
        version_id="cbv1",
        snapshot_key=materialized.snapshot_key,
        source_digest=materialized.source_digest,
        object_storage=_ObjectStorage({materialized.snapshot_key: materialized.snapshot_bytes}),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=MagicMock(),
        policy=_CODE_POLICY,
        source_reader=source_reader,
        version_id="cbv1",
    )

    rendered = await tool.read_code("src/main.py")

    assert "provenance=published_version" in rendered
    assert "version=cbv1" in rendered


@pytest.mark.anyio
async def test_semantic_search_uses_hybrid_retriever_with_bound_version():
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=CodeSearchResponse(
            items=[
                CodeSearchResult(
                    version_id="cbv1",
                    path="src/user_service.py",
                    lines=(10, 12),
                    symbol_id="sym1",
                    sources=("lexical", "vector"),
                    score=0.42,
                    content="def create_user(): pass",
                )
            ],
            capabilities={"lexical_search": True, "vector_search": True},
            degraded_reasons=[],
        )
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=MagicMock(),
        policy=_CODE_POLICY,
        version_id="cbv1",
        retriever=retriever,
    )

    result = await tool.semantic_search("create user", limit=5)

    retriever.retrieve.assert_awaited_once_with("cb1", "cbv1", "create user", 5)
    assert "src/user_service.py:10-12" in result
    assert "sources=lexical,vector" in result
    assert "def create_user" in result


@pytest.mark.anyio
async def test_semantic_search_caps_requested_limit_with_the_run_policy():
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=CodeSearchResponse(
            items=[],
            capabilities={"lexical_search": True, "vector_search": False},
            degraded_reasons=[],
        )
    )
    policy = CodebaseRetrievalRunPolicy(
        vector_enabled=False,
        retrieval=CodebaseRetrievalPolicy(final_top_k=2),
    )
    tool = CodebaseTool(
        uow_factory=lambda: MagicMock(),
        codebase_id="cb1",
        sandbox=MagicMock(),
        policy=policy,
        version_id="cbv1",
        retriever=retriever,
    )

    await tool.semantic_search("create user", limit=99)

    retriever.retrieve.assert_awaited_once_with("cb1", "cbv1", "create user", 2)
