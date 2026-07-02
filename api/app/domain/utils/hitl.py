#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Human-in-the-loop helpers."""
from __future__ import annotations

import fnmatch
from typing import Any, Dict, List, Optional


PLAN_APPROVAL_PHASE = "plan_approval"
TOOL_APPROVAL_PHASE = "tool_approval"
TAKEOVER_PHASE = "takeover"


def tool_matches_risk_list(tool_name: str, risk_list: List[str]) -> bool:
    normalized = tool_name.lower()
    for pattern in risk_list:
        pat = pattern.lower()
        if pat.endswith("*"):
            if fnmatch.fnmatch(normalized, pat):
                return True
        elif normalized == pat or normalized.startswith(pat + "_"):
            return True
    return False


def parse_gate_action(message: str) -> tuple[str, str]:
    """Parse structured gate resume message."""
    text = (message or "").strip()
    if not text:
        return "unknown", ""
    if text.startswith("reject:"):
        return "reject", text[7:].strip()
    if text.startswith("approve_with_edits"):
        return "approve_with_edits", text
    if text.startswith("approve_same"):
        return "approve_same", text
    if text.startswith("approve"):
        return "approve", text
    if text.startswith("reject"):
        return "reject", text.replace("reject", "", 1).strip(": ").strip()
    if text in {"takeover", "skip"}:
        return text, text
    return "unknown", text


def derive_risk_tools_from_plan(steps: List[Any], risk_list: List[str]) -> List[str]:
    """Heuristic: infer likely risky tools from plan step descriptions."""
    keywords = {
        "write_file": ["写", "修改", "创建文件", "write", "edit file", "save file"],
        "shell_execute": ["shell", "命令", "终端", "bash", "执行命令"],
        "replace_in_file": ["替换", "replace"],
        "mcp_*": ["mcp", "slack", "飞书", "jira"],
        "a2a": ["远程 agent", "a2a", "delegate"],
    }
    found: List[str] = []
    blob = " ".join(getattr(s, "description", str(s)) for s in steps).lower()
    for tool in risk_list:
        base = tool.replace("*", "")
        keys = keywords.get(tool, keywords.get(base, [base.replace("_", " ")]))
        if any(k.lower() in blob for k in keys):
            found.append(tool)
    if not found:
        found = [t for t in risk_list if t in {"write_file", "shell_execute", "mcp_*", "a2a"}][:3]
    return found


def merge_pending_metadata(
        current: Optional[Dict[str, Any]],
        patch: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(current or {})
    merged.update(patch)
    return merged
