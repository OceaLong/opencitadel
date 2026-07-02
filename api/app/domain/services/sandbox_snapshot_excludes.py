#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared tar exclude patterns for sandbox workspace snapshots."""

WORKSPACE_SNAPSHOT_EXCLUDE_DIRS = (
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    "target",
    ".idea",
    ".vscode",
    "coverage",
    ".snapshots",
    ".browser-profile",
)

WORKSPACE_SNAPSHOT_EXCLUDE_GLOBS = (
    "*.tgz",
)


def build_tar_exclude_args() -> str:
    """Build tar --exclude arguments for workspace snapshots."""
    parts: list[str] = []
    for directory in WORKSPACE_SNAPSHOT_EXCLUDE_DIRS:
        parts.append(f"--exclude='{directory}'")
    for pattern in WORKSPACE_SNAPSHOT_EXCLUDE_GLOBS:
        parts.append(f"--exclude='{pattern}'")
    return " ".join(parts)
