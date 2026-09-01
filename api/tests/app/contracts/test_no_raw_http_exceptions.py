"""Interface error-handling contract (E8).

HTTP endpoints must surface failures through the domain ``AppException``
hierarchy (which carries ``error_key``/``error_params`` for frontend i18n)
rather than raising bare Starlette/FastAPI ``HTTPException`` -- the latter
reaches the central handler with only a raw ``detail`` string and no
``error_key``, so the UI can only echo the original (Chinese) message.

This contract fails closed on any ``raise HTTPException`` or ``raise
HTTPError`` under ``app/interfaces/endpoints`` so the regression can never
be reintroduced silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
ENDPOINTS_DIR = REPOSITORY_ROOT / "api/app/interfaces/endpoints"

_FORBIDDEN_RAISES = {"HTTPException", "HTTPError"}


def _raised_callable_name(node: ast.Raise) -> str | None:
    """Return the bare callable name for ``raise Name(...)`` / ``raise Name``."""
    exc = node.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def test_endpoints_never_raise_bare_http_exception() -> None:
    offenders: list[str] = []
    for path in sorted(ENDPOINTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise):
                name = _raised_callable_name(node)
                if name in _FORBIDDEN_RAISES:
                    rel = path.relative_to(REPOSITORY_ROOT).as_posix()
                    offenders.append(f"{rel}:{node.lineno} raise {name}")

    assert offenders == [], (
        "Endpoints must raise app.domain.errors.AppException subclasses "
        "(carrying error_key) instead of bare HTTPException:\n" + "\n".join(offenders)
    )
