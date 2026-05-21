#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.tools.tool_names import (
    LEGACY_TOOL_NAME_ALIASES,
    normalize_allowed_tool_names,
    normalize_tool_name,
)


def test_normalize_tool_name_maps_legacy_file_tools():
    assert normalize_tool_name("file_read") == "read_file"
    assert normalize_tool_name("file_write") == "write_file"
    assert normalize_tool_name("file_str_replace") == "replace_in_file"
    assert normalize_tool_name("write_file") == "write_file"


def test_normalize_allowed_tool_names():
    assert normalize_allowed_tool_names(["file_read", "search_web"]) == [
        "read_file",
        "search_web",
    ]
    assert normalize_allowed_tool_names(None) is None


def test_legacy_aliases_cover_builtin_skill_old_names():
    for old_name in ("file_read", "file_write", "file_str_replace"):
        assert old_name in LEGACY_TOOL_NAME_ALIASES
