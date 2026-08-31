"""Repository-wide quality policy contracts."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

from sqlalchemy import DateTime

from app.infrastructure.models.registry import model_metadata

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_root_ruff_policy_has_no_debt_waivers() -> None:
    config = tomllib.loads((REPOSITORY_ROOT / "ruff.toml").read_text(encoding="utf-8"))
    lint = config["lint"]

    forbidden = {"ignore", "extend-ignore", "per-file-ignores"}
    assert forbidden.isdisjoint(lint)
    assert config["target-version"] == "py312"


def test_all_database_datetimes_are_timezone_aware() -> None:
    naive_columns = sorted(
        f"{table.name}.{column.name}"
        for table in model_metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    )

    assert naive_columns == []


def test_datetime_default_factories_are_timezone_aware() -> None:
    offenders = []
    for path in (REPOSITORY_ROOT / "api/app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "default_factory=datetime.now" in source or "default_factory=datetime.utcnow" in source:
            offenders.append(path.relative_to(REPOSITORY_ROOT).as_posix())

    assert offenders == []


def test_runtime_i18n_key_manifest_matches_python_emitters() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "contracts/i18n-runtime-keys.json").read_text(encoding="utf-8")
    )
    emitted = {"apiErrorKeys": set(), "notificationKeys": set()}
    key_groups = {"error_key": "apiErrorKeys", "i18n_key": "notificationKeys"}

    for path in (REPOSITORY_ROOT / "api/app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if (
                        keyword.arg in key_groups
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        emitted[key_groups[keyword.arg]].add(keyword.value.value)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = [*node.args.posonlyargs, *node.args.args]
                defaults = [None] * (len(arguments) - len(node.args.defaults)) + list(
                    node.args.defaults
                )
                for argument, default in zip(arguments, defaults, strict=True):
                    if (
                        argument.arg in key_groups
                        and isinstance(default, ast.Constant)
                        and isinstance(default.value, str)
                    ):
                        emitted[key_groups[argument.arg]].add(default.value)

    normalized_manifest = {group: sorted(keys) for group, keys in manifest.items()}
    assert {group: sorted(keys) for group, keys in emitted.items()} == normalized_manifest


def test_execution_cutover_evidence_has_no_hand_maintained_test_counts() -> None:
    paths = (
        REPOSITORY_ROOT / "docs/architecture/execution-kernel-cutover-evidence.md",
        REPOSITORY_ROOT / "docs/architecture/execution-kernel-cutover-evidence.zh-CN.md",
    )
    volatile_count = re.compile(
        r"\b\d[\d,]*\s+(?:passed|skipped|tests?\s+passed|个测试通过|条依赖)",
        re.IGNORECASE,
    )

    offenders = {
        path.relative_to(REPOSITORY_ROOT).as_posix(): volatile_count.findall(
            path.read_text(encoding="utf-8")
        )
        for path in paths
        if volatile_count.search(path.read_text(encoding="utf-8"))
    }

    assert offenders == {}
