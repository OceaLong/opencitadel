from __future__ import annotations

import ast
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[3]
_PRODUCTION_ROOTS = (_API_ROOT / "app",)
_WRITE_PREFIXES = (
    "add",
    "collect",
    "consume",
    "create",
    "decrement",
    "delete",
    "fail",
    "flush",
    "increment",
    "insert",
    "mark",
    "publish",
    "receive",
    "remove",
    "replace",
    "revoke",
    "save",
    "touch",
    "transition",
    "update",
    "upsert",
)


def _root_name(expression: ast.expr) -> str | None:
    while isinstance(expression, ast.Attribute):
        expression = expression.value
    return expression.id if isinstance(expression, ast.Name) else None


def _uow_name(item: ast.withitem) -> str | None:
    if not isinstance(item.optional_vars, ast.Name):
        return None
    expression = item.context_expr
    if not isinstance(expression, ast.Call):
        return None
    function = expression.func
    name = (
        function.attr
        if isinstance(function, ast.Attribute)
        else function.id
        if isinstance(function, ast.Name)
        else ""
    )
    return item.optional_vars.id if "uow" in name else None


def _calls_for(block: ast.AsyncWith, uow_name: str) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(block):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if _root_name(node.func.value) == uow_name:
            calls.add(node.func.attr)
    return calls


def test_every_direct_uow_repository_write_has_an_explicit_commit() -> None:
    violations: list[str] = []
    for root in _PRODUCTION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for block in (node for node in ast.walk(tree) if isinstance(node, ast.AsyncWith)):
                for item in block.items:
                    uow_name = _uow_name(item)
                    if uow_name is None:
                        continue
                    calls = _calls_for(block, uow_name)
                    writes = sorted(
                        method
                        for method in calls
                        if method != "commit" and method.startswith(_WRITE_PREFIXES)
                    )
                    if writes and "commit" not in calls:
                        violations.append(f"{path}:{block.lineno} writes={','.join(writes)}")

    assert violations == [], "\n".join(violations)
