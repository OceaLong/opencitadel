from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.application.ports.crypto import TokenCodecError
from app.composition.types import ApiRuntime
from app.domain.errors import ForbiddenError
from app.domain.models.user import UserStatus
from app.domain.runtime_policy import TrafficPolicy
from app.interfaces.auth_context import get_principal
from app.interfaces.middleware.api_cache_policy import ApiCachePolicyMiddleware
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from app.interfaces.middleware.csrf import CsrfMiddleware
from app.main import _install_application
from core.config import DeploymentSettings


def _runtime(**values) -> ApiRuntime:
    runtime = object.__new__(ApiRuntime)
    for name, value in values.items():
        object.__setattr__(runtime, name, value)
    return runtime


class _UnitOfWork:
    def __init__(self, user) -> None:
        self.user = SimpleNamespace(get_by_id=self._get_user)
        self.team = SimpleNamespace(
            list_for_user=self._list_teams,
            get_member=self._get_member,
        )
        self._user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def _get_user(self, _user_id: str):
        return self._user

    async def _list_teams(self, _user_id: str):
        return []

    async def _get_member(self, _team_id: str, _user_id: str):
        return None


def test_auth_context_uses_the_lifespan_runtime_token_codec_and_uow() -> None:
    """Removing either runtime dependency must make cookie authentication fail."""

    user = SimpleNamespace(
        id="user-1",
        status=UserStatus.ACTIVE,
        token_version=7,
        global_role="user",
    )

    class TokenCodec:
        def decode(self, token: str, expected_type: str):
            assert (token, expected_type) == ("valid-token", "access")
            return {"sub": "user-1", "ver": 7}

    app = FastAPI()
    app.state.runtime = _runtime(
        token_codec=TokenCodec(),
        uow_factory=lambda: _UnitOfWork(user),
    )
    app.add_middleware(AuthContextMiddleware)

    @app.get("/")
    async def principal_id():
        principal = get_principal()
        return {"user_id": principal.user_id if principal else None}

    with TestClient(app) as client:
        client.cookies.set("access_token", "valid-token")
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-1"}


def test_auth_context_treats_token_port_errors_as_anonymous() -> None:
    class TokenCodec:
        def decode(self, _token: str, expected_type: str):
            assert expected_type == "access"
            raise TokenCodecError("invalid")

    app = FastAPI()
    app.state.runtime = _runtime(token_codec=TokenCodec(), uow_factory=lambda: None)
    app.add_middleware(AuthContextMiddleware)

    @app.get("/")
    async def principal_id():
        principal = get_principal()
        return {"user_id": principal.user_id if principal else None}

    with TestClient(app) as client:
        client.cookies.set("access_token", "invalid-token")
        response = client.get("/")

    assert response.json() == {"user_id": None}


def test_csrf_middleware_uses_the_lifespan_runtime_verifier() -> None:
    calls: list[str] = []

    class CsrfVerifier:
        def verify_request(self, request) -> None:
            calls.append(request.url.path)

    app = FastAPI()
    app.state.runtime = _runtime(csrf_service=CsrfVerifier())
    app.add_middleware(CsrfMiddleware)

    @app.post("/api/resource")
    async def mutate():
        return {"ok": True}

    with TestClient(app) as client:
        client.cookies.set("access_token", "token")
        response = client.post("/api/resource")

    assert response.status_code == 200
    assert calls == ["/api/resource"]


def test_api_cache_policy_forbids_reusing_authenticated_projections() -> None:
    app = FastAPI()
    app.add_middleware(ApiCachePolicyMiddleware)

    @app.get("/api/auth/me")
    async def authenticated_projection():
        return {"user_id": "user-1"}

    @app.get("/public")
    async def public_projection():
        return {"ok": True}

    with TestClient(app) as client:
        authenticated = client.get("/api/auth/me")
        public = client.get("/public")

    assert authenticated.headers["Cache-Control"] == "no-store"
    assert authenticated.headers["Pragma"] == "no-cache"
    assert "Cache-Control" not in public.headers
    assert "Pragma" not in public.headers


def test_api_cache_policy_does_not_add_a_stream_cancellation_boundary() -> None:
    assert not issubclass(ApiCachePolicyMiddleware, BaseHTTPMiddleware)


def test_application_cache_policy_covers_csrf_short_circuit_responses() -> None:
    class RejectCsrf:
        def verify_request(self, _request) -> None:
            raise ForbiddenError("CSRF validation failed")

    class PolicyReader:
        async def active_operations(self, **_kwargs):
            return SimpleNamespace(
                revision=SimpleNamespace(
                    policy=SimpleNamespace(traffic=TrafficPolicy(rate_limit_enabled=False))
                )
            )

    app = FastAPI()
    app.state.runtime = _runtime(
        csrf_service=RejectCsrf(),
        runtime_policy_reader=PolicyReader(),
    )
    _install_application(app, DeploymentSettings(env="test"))

    @app.post("/api/cache-policy-probe")
    async def mutate():
        return {"ok": True}

    with TestClient(app) as client:
        client.cookies.set("access_token", "authenticated")
        response = client.post("/api/cache-policy-probe")

    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
