"""Explicit resource-route RBAC matrix for the read-only Auditor role."""

from app.interfaces.auth_dependencies import require_non_auditor
from app.interfaces.endpoints.knowledge_base_routes import router as kb_router


def _write_dependencies(router):
    return {
        f"{next(iter(route.methods))}:{route.path}": {
            dependency.call for dependency in route.dependant.dependencies
        }
        for route in router.routes
        if hasattr(route, "dependant")
    }


def test_resource_mutation_route_matrix_requires_non_auditor():
    """Catches a newly-added resource mutation silently omitting the guard."""
    kb = _write_dependencies(kb_router)
    required = {f"kb:{path}": deps for path, deps in kb.items()}
    for name in (
        "kb:POST:/knowledge-bases",
        "kb:DELETE:/knowledge-bases/{kb_id}",
        "kb:POST:/knowledge-bases/{kb_id}/documents",
        "kb:DELETE:/knowledge-bases/{kb_id}/documents/{doc_id}",
        "kb:POST:/knowledge-bases/{kb_id}/sessions",
        "kb:POST:/knowledge-bases/{kb_id}/builds",
        "kb:POST:/knowledge-bases/{kb_id}/builds/{build_id}/retry",
        "kb:POST:/knowledge-bases/{kb_id}/builds/{build_id}/cancel",
    ):
        assert require_non_auditor in required[name]
