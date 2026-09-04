"""Tool assembly is spec-table driven; mode exposure comes from ToolSpec (D10)."""

from app.application.execution.agent_tool_catalog import _TOOL_ASSEMBLY
from app.domain.models.session_mode import SessionMode
from app.domain.services.tools.tool_registry import ToolRegistry


def _pack_names_for(mode: SessionMode) -> set[str]:
    return {assembly.spec.name for assembly in _TOOL_ASSEMBLY if mode in assembly.spec.modes}


def test_ask_mode_spec_excludes_shell_file_browser():
    names = _pack_names_for(SessionMode.ASK)

    assert "file" not in names
    assert "shell" not in names
    assert "browser" not in names
    assert {"mcp", "a2a", "knowledge_base"} <= names


def test_agent_mode_spec_includes_execution_and_vision_packs():
    names = _pack_names_for(SessionMode.AGENT)

    assert {"file", "shell", "browser", "search", "vision", "memory", "artifact"} <= names


def test_dual_manifest_builders_are_gone():
    # build_default_tools/build_ask_tools 双清单已删除：唯一装配点是 spec 表。
    assert not hasattr(ToolRegistry, "build_default_tools")
    assert not hasattr(ToolRegistry, "build_ask_tools")


def test_retrieval_marker_is_declared_on_knowledge_base_spec():
    retrieval = {
        assembly.spec.name: assembly.spec.retrieval_tool
        for assembly in _TOOL_ASSEMBLY
        if assembly.spec.retrieval_tool is not None
    }

    assert retrieval == {"knowledge_base": "kb_search"}
