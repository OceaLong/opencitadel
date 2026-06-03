#!/usr/bin/env python
# -*- coding: utf-8 -*-
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


def normalize_tool_name(name: str) -> str:
    """将旧工具名映射为当前运行时注册名。"""
    return LEGACY_TOOL_NAME_ALIASES.get(name, name)


def normalize_allowed_tool_names(names: Optional[List[str]]) -> Optional[List[str]]:
    """规范化 Skill 白名单中的工具名。"""
    if not names:
        return names
    return [normalize_tool_name(name) for name in names]
