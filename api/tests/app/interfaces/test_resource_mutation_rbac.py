"""Explicit resource-route RBAC matrix for the read-only Auditor role."""
from app.interfaces.auth_dependencies import require_non_auditor
from app.interfaces.endpoints.codebase_routes import router as codebase_router
from app.interfaces.endpoints.knowledge_base_routes import router as kb_router


def _write_dependencies(router):
    return {
        f"{next(iter(route.methods))}:{route.path}": {dependency.call for dependency in route.dependant.dependencies}
        for route in router.routes
        if hasattr(route, "dependant")
    }


def test_resource_mutation_route_matrix_requires_non_auditor():
    """Catches a newly-added resource mutation silently omitting the guard."""
    kb = _write_dependencies(kb_router)
    codebase = _write_dependencies(codebase_router)
    required = {
        **{f"kb:{path}": deps for path, deps in kb.items()},
        **{f"cb:{path}": deps for path, deps in codebase.items()},
    }
    for name in (
        "kb:POST:/knowledge-bases", "kb:DELETE:/knowledge-bases/{kb_id}", "kb:POST:/knowledge-bases/{kb_id}/documents",
        "kb:DELETE:/knowledge-bases/{kb_id}/documents/{doc_id}", "kb:POST:/knowledge-bases/{kb_id}/reindex",
        "kb:POST:/knowledge-bases/{kb_id}/sessions", "cb:POST:/codebases", "cb:POST:/codebases/{codebase_id}/source",
        "kb:POST:/knowledge-bases/{kb_id}/builds",
        "kb:POST:/knowledge-bases/{kb_id}/builds/{build_id}/retry",
        "kb:POST:/knowledge-bases/{kb_id}/builds/{build_id}/cancel",
        "cb:POST:/codebases/{codebase_id}/reanalyze", "cb:POST:/codebases/{codebase_id}/snapshots",
        "cb:POST:/codebases/{codebase_id}/sessions", "cb:DELETE:/codebases/{codebase_id}",
    ):
        assert require_non_auditor in required[name]


def test_read_only_download_has_no_mutation_guard():
    """Catches a compatibility GET changing back into a mutating route."""
    assert require_non_auditor not in _write_dependencies(codebase_router)["GET:/codebases/{codebase_id}/download"]
