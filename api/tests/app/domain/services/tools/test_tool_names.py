from app.domain.services.tools.tool_names import (
    expand_tool_patterns,
    is_tool_allowed,
    references_a2a_tools,
)


def test_is_tool_allowed_none_means_all():
    assert is_tool_allowed("read_file", None) is True
    assert is_tool_allowed("mcp_jina_read_url", None) is True


def test_is_tool_allowed_exact_match():
    allowed = ["read_file", "write_file"]
    assert is_tool_allowed("read_file", allowed) is True
    assert is_tool_allowed("search_web", allowed) is False


def test_is_tool_allowed_mcp_wildcard():
    allowed = ["mcp_*"]
    assert is_tool_allowed("mcp_jina_read_url", allowed) is True
    assert is_tool_allowed("mcp_amap_maps_search", allowed) is True
    assert is_tool_allowed("read_file", allowed) is False


def test_is_tool_allowed_mcp_server_prefix():
    allowed = ["mcp_jina_*"]
    assert is_tool_allowed("mcp_jina_read_url", allowed) is True
    assert is_tool_allowed("mcp_amap_search", allowed) is False


def test_is_tool_allowed_a2a_group():
    allowed = ["a2a"]
    assert is_tool_allowed("get_remote_agent_cards", allowed) is True
    assert is_tool_allowed("call_remote_agent", allowed) is True
    assert is_tool_allowed("read_file", allowed) is False


def test_empty_allowlist_denies_every_tool():
    # D11 显式语义：[] = 禁全部（None = 不限制，见上）。
    for name in ("read_file", "mcp_jina_read_url", "get_remote_agent_cards"):
        assert is_tool_allowed(name, []) is False


def test_expand_tool_patterns_expands_a2a_group_to_real_tool_names():
    assert expand_tool_patterns(["a2a", "read_file"]) == [
        "call_remote_agent",
        "get_remote_agent_cards",
        "read_file",
    ]
    assert expand_tool_patterns(None) is None
    assert expand_tool_patterns([]) == []


def test_references_a2a_tools_detects_group_token_and_real_names():
    assert references_a2a_tools(["a2a"]) is True
    assert references_a2a_tools(["call_remote_agent"]) is True
    assert references_a2a_tools(["get_remote_agent_*"]) is True
    assert references_a2a_tools(["a2a_legacy_prefix"]) is False
    assert references_a2a_tools(["read_file"]) is False
    assert references_a2a_tools([]) is False
    assert references_a2a_tools(None) is False
