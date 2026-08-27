"""Executable import boundaries for explicit runtime composition."""

from __future__ import annotations

import ast
import re
from pathlib import Path

API_ROOT = Path(__file__).parents[3]
APP_ROOT = API_ROOT / "app"

FORBIDDEN_LEGACY_PATTERNS = (
    r"\bdependency_injector\b",
    r"\bProvide\s*\[",
    r"@inject\b",
    r"\bget_deployment_settings\b",
    r"\bget_postgres\b",
    r"\bget_redis\b",
    r"\bget_uow\b",
    r"\bget_cos\b",
    r"\bget_llm_circuit_breaker\b",
    r"\bget_api_container\b",
    r"\bget_execution_kernel_container\b",
)

SETTINGS_ENTRYPOINTS = {
    "app/execution_kernel_health.py",
    "app/execution_kernel_main.py",
    "app/infrastructure/external/sandbox/broker.py",
    "app/main.py",
    "app/migrate.py",
    "app/migrate_storage.py",
    "app/seed_demo.py",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_interfaces_depend_only_on_application_domain_and_composition() -> None:
    """HTTP adapters must not reconstruct infrastructure or deployment state."""
    violations: list[str] = []
    forbidden = ("app.infrastructure", "core.config", "dependency_injector", "sqlalchemy")

    for path in sorted((API_ROOT / "app/interfaces").rglob("*.py")):
        violations.extend(
            f"{path.relative_to(API_ROOT)} -> {imported}"
            for imported in sorted(_imports(path))
            if imported.startswith(forbidden)
        )

    assert violations == []


def test_application_factory_has_no_container_or_global_app() -> None:
    import app.main as main

    assert callable(main.create_app)
    assert not hasattr(main, "app")
    assert all(not name.startswith("app.container") for name in _imports(Path(main.__file__)))


def test_legacy_container_and_global_resource_symbols_are_absent() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        violations.extend(
            f"{path.relative_to(API_ROOT)} -> {pattern}"
            for pattern in FORBIDDEN_LEGACY_PATTERNS
            if re.search(pattern, source)
        )

    assert not (APP_ROOT / "container.py").exists()
    assert violations == []


def test_only_process_entrypoints_load_deployment_settings() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(API_ROOT).as_posix()
        if (
            "load_deployment_settings" in path.read_text(encoding="utf-8")
            and relative not in SETTINGS_ENTRYPOINTS
        ):
            violations.append(relative)

    assert violations == []


def test_asyncio_primitives_are_never_owned_by_modules_or_classes() -> None:
    primitive_names = {"Condition", "Event", "Lock", "Queue", "Semaphore"}
    violations: list[str] = []

    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
                and node.func.attr in primitive_names
            ):
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                owner = parents.get(owner)
            if owner is None:
                violations.append(
                    f"{path.relative_to(API_ROOT)}:{node.lineno} -> asyncio.{node.func.attr}"
                )

    assert violations == []
