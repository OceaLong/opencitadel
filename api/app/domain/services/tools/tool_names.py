from fnmatch import fnmatch

# Skill 白名单中的 A2A 工具组标识
A2A_GROUP_TOKEN = "a2a"
A2A_TOOL_NAMES = frozenset({"get_remote_agent_cards", "call_remote_agent"})


def is_tool_allowed(tool_name: str, allowed_patterns: list[str] | None) -> bool:
    """判断工具名是否匹配 Skill 白名单（支持精确匹配与通配符）。

    支持的通配模式示例:
    - ``mcp_*`` — 所有 MCP 动态工具
    - ``mcp_jina_*`` — 指定 MCP 服务下的工具
    - ``a2a`` — A2A 工具组（get_remote_agent_cards / call_remote_agent）
    """
    if allowed_patterns is None:
        return True
    for pattern in allowed_patterns:
        if pattern == A2A_GROUP_TOKEN:
            if tool_name in A2A_TOOL_NAMES:
                return True
            continue
        if "*" in pattern:
            if fnmatch(tool_name, pattern):
                return True
        elif tool_name == pattern:
            return True
    return False
