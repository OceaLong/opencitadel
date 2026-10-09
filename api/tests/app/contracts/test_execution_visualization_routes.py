"""Mounted-route specificity and typed wire contracts; no service or database startup."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Match

from app.interfaces.endpoints.routes import create_api_routes


@pytest.fixture(scope="module")
def application():
    app = FastAPI()
    app.include_router(create_api_routes(), prefix="/api")
    return app


def mounted_routes(application):
    # FastAPI newer versions retain include branches instead of flattening them.
    # Effective contexts preserve mount prefixes and declaration order.
    for route in application.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


@pytest.mark.parametrize(
    ("method", "path", "endpoint"),
    [
        ("GET", "/execution-analysis/summary", "summary"),
        ("GET", "/execution-analysis/runs", "runs"),
        ("GET", "/execution-analysis/preferences", "get_preferences"),
        ("POST", "/execution-analysis/exports", "create_export"),
        ("GET", "/execution-analysis/exports/{id}", "get_export"),
        ("GET", "/execution-analysis/exports/{id}/content", "download_export"),
        ("GET", "/execution-analysis/diff-jobs/{id}", "get_artifact_diff"),
        ("POST", "/execution-comparisons", "create_comparison"),
        ("GET", "/execution-comparisons/{id}", "get_comparison"),
        ("GET", "/execution-comparisons/{id}/body", "retained_body"),
        ("POST", "/execution-comparisons/{id}/alignments", "align_comparison"),
        ("POST", "/evaluation/batches/preflight", "preflight"),
        ("GET", "/evaluation/environments/inventory", "inventory"),
        ("GET", "/evaluation/batches/{id}/environments", "environments"),
        ("GET", "/evaluation/archives/pins", "readable_pins"),
        ("GET", "/execution-runs/{id}/view", "get_view"),
        ("GET", "/execution-runs/{id}/timeline", "get_timeline"),
    ],
)
def test_first_mounted_route_is_specific(application, method, path, endpoint):
    scope = {"type": "http", "path": "/api" + path.format(id=uuid4()), "method": method}
    matching = [
        route for route in mounted_routes(application) if route.matches(scope)[0] == Match.FULL
    ]
    assert matching, (method, path)
    assert matching[0].endpoint.__name__ == endpoint


def test_visualization_json_success_responses_have_concrete_models(application):
    schema = application.openapi()
    selected = [
        route
        for route in mounted_routes(application)
        if isinstance(getattr(route, "original_route", route), APIRoute)
        and route.path.startswith(
            ("/api/execution-analysis", "/api/execution-comparisons", "/api/evaluation/")
        )
    ]
    assert selected
    for route in selected:
        if route.endpoint.__name__ == "download_export" or route.path.endswith("/events/stream"):
            continue  # Authenticated streaming bytes/SSE, covered by behavioral tests.
        assert route.response_model is not None, route.path
        for method in route.methods:
            operation = schema["paths"][route.path][method.lower()]
            response = operation["responses"][str(route.status_code or 200)]
            body = response["content"]["application/json"]["schema"]
            assert "$ref" in body, (route.path, body)
            model = schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
            assert "data" in model["properties"], route.path
            assert model["properties"]["data"] != {}, route.path
