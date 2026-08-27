"""Architecture contracts for a greenfield codebase with no debt waivers."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_import_contracts_have_no_ignored_dependencies() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    contracts = config["tool"]["importlinter"]["contracts"]

    ignored = {
        contract["name"]: contract["ignore_imports"]
        for contract in contracts
        if contract.get("ignore_imports")
    }

    assert ignored == {}
