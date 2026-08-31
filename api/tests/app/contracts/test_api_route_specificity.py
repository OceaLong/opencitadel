from fastapi import FastAPI

from app.interfaces.endpoints.routes import create_api_routes


def _resolve_get(path: str) -> str:
    app = FastAPI()
    app.include_router(create_api_routes(), prefix="/api")
    full_path = f"/api{path}"
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if not callable(contexts):
            continue
        for context in contexts():
            if "GET" in context.methods and context.path_regex.fullmatch(full_path):
                return context.name
    raise AssertionError(f"no GET route resolves {path}")


def test_static_audit_routes_are_not_shadowed_by_log_detail() -> None:
    assert _resolve_get("/admin/audit/summary") == "audit_summary"
    assert _resolve_get("/admin/audit/export") == "export_audit_logs"
    assert _resolve_get("/admin/audit/verify-chain") == "verify_chain"
    assert _resolve_get("/admin/audit/logs/log-1") == "get_audit_log"
