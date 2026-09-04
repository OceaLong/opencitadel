from fnmatch import fnmatch

# Skill 白名单中的 A2A 工具组标识（单源，D11）：组 token 展开为真实工具名。
A2A_GROUP_TOKEN = "a2a"
A2A_TOOL_NAMES = frozenset({"get_remote_agent_cards", "call_remote_agent"})


def expand_tool_patterns(patterns: list[str] | None) -> list[str] | None:
    """展开白名单中的组标识为真实工具名；None（不限制）原样返回。"""
    if patterns is None:
        return None
    expanded: list[str] = []
    for pattern in patterns:
        if pattern == A2A_GROUP_TOKEN:
            expanded.extend(sorted(A2A_TOOL_NAMES))
        else:
            expanded.append(pattern)
    return expanded


def references_a2a_tools(patterns: list[str] | None) -> bool:
    """白名单是否声明了 A2A 工具（组 token 或匹配真实工具名的模式）。

    None（不限制）不视为显式声明——未声明的 Skill 不强制绑定 A2A server refs。
    """
    if patterns is None:
        return False
    return any(is_tool_allowed(name, patterns) for name in A2A_TOOL_NAMES)


def is_tool_allowed(tool_name: str, allowed_patterns: list[str] | None) -> bool:
    """判断工具名是否匹配 Skill 白名单（支持精确匹配与通配符）。

    白名单语义（D11）：``None`` = 不限制；``[]`` = 禁全部。
    支持的通配模式示例:
    - ``mcp_*`` — 所有 MCP 动态工具
    - ``mcp_jina_*`` — 指定 MCP 服务下的工具
    - ``a2a`` — A2A 工具组（get_remote_agent_cards / call_remote_agent）
    """
    if allowed_patterns is None:
        return True
    for pattern in expand_tool_patterns(allowed_patterns) or []:
        if "*" in pattern:
            if fnmatch(tool_name, pattern):
                return True
        elif tool_name == pattern:
            return True
    return False
