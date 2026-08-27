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

WORKSPACE_SNAPSHOT_EXCLUDE_GLOBS = ("*.tgz",)


def build_tar_exclude_args() -> str:
    """Build tar --exclude arguments for workspace snapshots."""
    parts = [f"--exclude='{directory}'" for directory in WORKSPACE_SNAPSHOT_EXCLUDE_DIRS]
    parts.extend(f"--exclude='{pattern}'" for pattern in WORKSPACE_SNAPSHOT_EXCLUDE_GLOBS)
    return " ".join(parts)
