from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.ports.crypto import TokenCodecError
from app.composition.types import ApiRuntime
from app.domain.models.user import UserStatus
from app.interfaces.auth_context import get_principal
from app.interfaces.middleware.auth_context import AuthContextMiddleware
from app.interfaces.middleware.csrf import CsrfMiddleware


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
