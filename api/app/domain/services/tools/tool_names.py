#!/usr/bin/env python
# -*- coding: utf-8 -*-
from fnmatch import fnmatch
from typing import List, Optional

# 兼容历史 Skill / 占位符 / 模型幻觉使用的旧工具名
LEGACY_TOOL_NAME_ALIASES = {
    "image_analyze": "analyze_image",
    "vision_analyze": "analyze_image",
    "file_read": "read_file",
    "file_write": "write_file",
    "file_str_replace": "replace_in_file",
    "file_find_in_content": "search_in_file",
    "file_find_by_name": "find_files",
    "file_list": "find_files",
}

# Skill 白名单中的 A2A 工具组标识
A2A_GROUP_TOKEN = "a2a"
A2A_TOOL_NAMES = frozenset({"get_remote_agent_cards", "call_remote_agent"})
MCP_GROUP_TOKEN = "mcp_*"


def normalize_tool_name(name: str) -> str:
    """将旧工具名映射为当前运行时注册名。"""
    return LEGACY_TOOL_NAME_ALIASES.get(name, name)


def normalize_allowed_tool_names(names: Optional[List[str]]) -> Optional[List[str]]:
    """规范化 Skill 白名单中的工具名。空列表视为不过滤（返回 None）。"""
    if not names:
        return None
    return [normalize_tool_name(name) for name in names]


def is_tool_allowed(tool_name: str, allowed_patterns: Optional[List[str]]) -> bool:
    """判断工具名是否匹配 Skill 白名单（支持精确匹配与通配符）。

    支持的通配模式示例:
    - ``mcp_*`` — 所有 MCP 动态工具
    - ``mcp_jina_*`` — 指定 MCP 服务下的工具
    - ``a2a`` — A2A 工具组（get_remote_agent_cards / call_remote_agent）
    """
    if allowed_patterns is None:
        return True
    normalized = normalize_tool_name(tool_name)
    for pattern in allowed_patterns:
        norm_pattern = normalize_tool_name(pattern)
        if norm_pattern == A2A_GROUP_TOKEN:
            if normalized in A2A_TOOL_NAMES:
                return True
            continue
        if "*" in norm_pattern:
            if fnmatch(normalized, norm_pattern):
                return True
        elif normalized == norm_pattern:
            return True
    return False
