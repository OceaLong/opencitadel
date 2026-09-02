"""Closed-world tool catalog and sandbox-boundary tests."""

from uuid import UUID

import pytest

from app.contexts.inference.tooling import BUILTIN_TOOL_CATALOG, _sandbox_path


def test_builtin_catalog_routes_files_to_file_effect_and_governs_writes() -> None:
    catalog = {entry["name"]: entry for entry in BUILTIN_TOOL_CATALOG}

    assert set(catalog) == {
        "browser.click",
        "browser.input",
        "browser.navigate",
        "browser.screenshot",
        "browser.scroll",
        "browser.view",
        "file.delete",
        "file.list",
        "file.read",
        "file.write",
        "shell.run",
    }
    assert catalog["file.read"]["effect_type"] == "file.operation"
    assert catalog["file.write"]["requires_approval"] is True
    assert catalog["shell.run"]["safety"] == "non_idempotent_write"
    assert catalog["browser.click"]["requires_approval"] is True


def test_sandbox_paths_are_run_isolated_and_cannot_escape() -> None:
    run_id = str(UUID(int=42))

    assert _sandbox_path(run_id, "src/main.py").endswith(
        "/0000000000000000000000000000002a/src/main.py"
    )
    with pytest.raises(ValueError, match="relative"):
        _sandbox_path(run_id, "../../etc/passwd")
    with pytest.raises(ValueError, match="relative"):
        _sandbox_path(run_id, "/etc/passwd")
