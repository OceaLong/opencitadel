#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.errors import BadRequestError, ForbiddenError, NotFoundError
from app.application.services.llm_model_service import LLMModelService
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import LLMModel, ModelCapabilities, ResourceVisibility
from app.domain.models.scope import OwnerScope
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


class _ModelRepo:
    def __init__(
        self,
        *,
        existing: LLMModel | None = None,
        default: LLMModel | None = None,
        count: int = 1,
        count_global: int = 1,
        scoped_visible: bool = True,
        global_models: list[LLMModel] | None = None,
    ):
        self.existing = existing
        self.default = default
        self._count = count
        self._count_global = count_global
        self._scoped_visible = scoped_visible
        self._global_models = list(global_models or [])
        self.saved: list[LLMModel] = []
        self.deleted: list[str] = []
        self.clear_default_calls = 0

    async def get_by_id(self, model_id: str, scope=None):
        if scope is not None and not self._scoped_visible:
            return None
        if self.existing and self.existing.id == model_id:
            return self.existing
        return None

    async def get_default(self):
        return self.default

    async def get_all(self, scope=None):
        return [self.existing] if self.existing else []

    async def get_all_global(self):
        return list(self._global_models)

    async def count(self):
        return self._count

    async def count_global(self):
        return self._count_global

    async def clear_default(self):
        self.clear_default_calls += 1

    async def save(self, model: LLMModel):
        self.saved.append(model.model_copy(deep=True))

    async def delete_by_id(self, model_id: str):
        self.deleted.append(model_id)


class _EndpointRepo:
    def __init__(self, endpoint: LLMEndpoint):
        self.endpoint = endpoint

    async def get_by_id(self, endpoint_id: str, scope=None):
        if endpoint_id == self.endpoint.id:
            return self.endpoint
        return None


class _PreferenceRepo:
    def __init__(
        self,
        *,
        global_model_id: str | None = None,
        scoped_model_id: str | None = None,
    ):
        self.global_model_id = global_model_id
        self.scoped_model_id = scoped_model_id
        self.saved: list[tuple[OwnerScope | None, str]] = []

    async def get_model_id(self, scope=None):
        return self.global_model_id if scope is None else self.scoped_model_id

    async def set_model_id(self, scope, model_id):
        self.saved.append((scope, model_id))
        if scope is None:
            self.global_model_id = model_id
        else:
            self.scoped_model_id = model_id


class _ModelUow:
    def __init__(
        self,
        model_repo: _ModelRepo,
        preference_repo: _PreferenceRepo | None = None,
    ):
        self.llm_model = model_repo
        self.llm_model_preference = preference_repo or _PreferenceRepo()
        self.llm_endpoint = _EndpointRepo(
            LLMEndpoint(
                id="endpoint-1",
                display_name="OpenAI",
                api_key="sk-test",
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _service(
    model_repo: _ModelRepo,
    preference_repo: _PreferenceRepo | None = None,
) -> LLMModelService:
    return LLMModelService(
        uow_factory=lambda: _ModelUow(model_repo, preference_repo),
        cipher=ApiKeyCipher("d" * 32),
    )


def _model(
    *,
    model_id: str = "model-1",
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE,
    is_default: bool = False,
    owner_user_id: str | None = "user-1",
    team_id: str | None = None,
) -> LLMModel:
    return LLMModel(
        id=model_id,
        endpoint_id="endpoint-1",
        display_name="Test Model",
        model_name="gpt-test",
        visibility=visibility,
        is_default=is_default,
        owner_user_id=owner_user_id,
        team_id=team_id,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_model_rejects_private_default_takeover():
    repo = _ModelRepo(count=5, count_global=1)

    with pytest.raises(BadRequestError, match="默认"):
        await _service(repo).create_model(
            _model(is_default=True),
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved == []
    assert repo.clear_default_calls == 0


@pytest.mark.anyio
async def test_first_private_model_does_not_become_system_default():
    repo = _ModelRepo(count=0, count_global=0)

    created = await _service(repo).create_model(
        _model(),
        scope=OwnerScope.personal("user-1"),
    )

    assert created.is_default is False
    assert repo.saved[0].is_default is False
    assert repo.clear_default_calls == 0


@pytest.mark.anyio
async def test_create_private_model_binds_team_scope():
    repo = _ModelRepo(count=3, count_global=1)

    await _service(repo).create_model(
        _model(owner_user_id=None),
        scope=OwnerScope.team("creator-1", "team-1"),
    )

    assert repo.saved[0].owner_user_id == "creator-1"
    assert repo.saved[0].team_id == "team-1"


@pytest.mark.anyio
async def test_update_model_rejects_default_flag_change():
    existing = _model(is_default=False)
    repo = _ModelRepo(existing=existing)

    with pytest.raises(BadRequestError, match="默认"):
        await _service(repo).update_model(
            existing.id,
            existing.model_copy(update={"is_default": True}),
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved == []
    assert repo.clear_default_calls == 0


@pytest.mark.anyio
async def test_explicit_model_resolution_rejects_cross_scope_id():
    victim = _model(
        model_id="victim-model",
        visibility=ResourceVisibility.PRIVATE,
        owner_user_id="victim-user",
    )
    fallback = _model(
        model_id="global-default",
        visibility=ResourceVisibility.GLOBAL,
        is_default=True,
        owner_user_id=None,
    )
    repo = _ModelRepo(
        existing=victim,
        default=fallback,
        scoped_visible=False,
    )

    with pytest.raises(NotFoundError):
        await _service(repo).resolve_model(
            victim.id,
            scope=OwnerScope.personal("attacker-user"),
        )


@pytest.mark.anyio
async def test_default_model_api_never_returns_private_model():
    private_default = _model(is_default=True)
    repo = _ModelRepo(default=private_default)

    assert await _service(repo).get_default_model() is None


@pytest.mark.anyio
async def test_create_global_model_requires_explicit_admin_capability():
    repo = _ModelRepo(count=1)
    global_model = _model(
        visibility=ResourceVisibility.GLOBAL,
        owner_user_id=None,
    )

    with pytest.raises(ForbiddenError):
        await _service(repo).create_model(
            global_model,
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved == []


@pytest.mark.anyio
async def test_update_global_model_requires_explicit_admin_capability():
    existing = _model(
        visibility=ResourceVisibility.GLOBAL,
        owner_user_id=None,
    )
    repo = _ModelRepo(existing=existing)

    with pytest.raises(ForbiddenError):
        await _service(repo).update_model(
            existing.id,
            existing.model_copy(update={"display_name": "changed"}),
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved == []


@pytest.mark.anyio
async def test_update_private_model_cannot_escalate_to_global():
    existing = _model()
    repo = _ModelRepo(existing=existing)

    with pytest.raises(ForbiddenError):
        await _service(repo).update_model(
            existing.id,
            existing.model_copy(update={"visibility": ResourceVisibility.GLOBAL}),
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved == []


@pytest.mark.anyio
async def test_delete_global_model_requires_explicit_admin_capability():
    existing = _model(
        visibility=ResourceVisibility.GLOBAL,
        owner_user_id=None,
    )
    repo = _ModelRepo(existing=existing, count=2)

    with pytest.raises(ForbiddenError):
        await _service(repo).delete_model(
            existing.id,
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.deleted == []


@pytest.mark.anyio
async def test_probe_global_model_requires_explicit_admin_capability():
    existing = _model(
        visibility=ResourceVisibility.GLOBAL,
        owner_user_id=None,
    )
    repo = _ModelRepo(existing=existing)

    with pytest.raises(ForbiddenError):
        await _service(repo).probe_multimodal(
            existing.id,
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved == []


@pytest.mark.anyio
async def test_first_global_model_becomes_default_even_when_private_models_exist():
    repo = _ModelRepo(count=5, count_global=0)
    preferences = _PreferenceRepo()
    global_model = _model(
        visibility=ResourceVisibility.GLOBAL,
        owner_user_id=None,
    )

    created = await _service(repo, preferences).create_model(
        global_model,
        scope=OwnerScope.personal("admin-1"),
        allow_global_mutation=True,
    )

    assert created.is_default is True
    assert repo.saved[0].is_default is False
    assert preferences.saved == [(None, global_model.id)]


@pytest.mark.anyio
async def test_last_global_model_cannot_be_deleted_while_private_models_exist():
    existing = _model(
        visibility=ResourceVisibility.GLOBAL,
        is_default=True,
        owner_user_id=None,
    )
    repo = _ModelRepo(
        existing=existing,
        count=5,
        count_global=1,
    )

    with pytest.raises(BadRequestError, match="全局"):
        await _service(repo).delete_model(
            existing.id,
            scope=OwnerScope.personal("admin-1"),
            allow_global_mutation=True,
        )

    assert repo.deleted == []


@pytest.mark.anyio
async def test_vision_default_resolution_never_selects_private_model():
    private_vision = _model().model_copy(
        update={
            "api_key": "sk-private",
            "capabilities": ModelCapabilities(vision=True),
        }
    )
    repo = _ModelRepo(
        existing=private_vision,
        default=None,
        global_models=[],
    )

    assert await _service(repo).resolve_vision_model() is None


@pytest.mark.anyio
async def test_create_global_model_rejects_generic_default_flag():
    repo = _ModelRepo(count=1, count_global=1)
    global_model = _model(
        visibility=ResourceVisibility.GLOBAL,
        is_default=True,
        owner_user_id=None,
    )

    with pytest.raises(BadRequestError, match="专用接口"):
        await _service(repo).create_model(
            global_model,
            scope=OwnerScope.personal("admin-1"),
            allow_global_mutation=True,
        )

    assert repo.clear_default_calls == 0
    assert repo.saved == []


@pytest.mark.anyio
async def test_explicit_model_resolution_requires_scope_argument():
    private_model = _model().model_copy(update={"api_key": "sk-private"})
    repo = _ModelRepo(existing=private_model)

    with pytest.raises(BadRequestError, match="访问作用域"):
        await _service(repo).resolve_model(private_model.id)


@pytest.mark.anyio
async def test_workspace_preference_is_resolved_before_system_default():
    preferred = _model(
        model_id="team-model",
        owner_user_id="creator-1",
        team_id="team-1",
    ).model_copy(update={"api_key": "sk-team"})
    repo = _ModelRepo(existing=preferred)
    preferences = _PreferenceRepo(
        global_model_id="global-model",
        scoped_model_id=preferred.id,
    )
    scope = OwnerScope.team("member-2", "team-1")

    resolved = await _service(repo, preferences).resolve_model(scope=scope)

    assert resolved.id == preferred.id


@pytest.mark.anyio
async def test_setting_workspace_preference_does_not_mutate_model_row():
    preferred = _model(model_id="personal-model").model_copy(
        update={"api_key": "sk-personal"}
    )
    repo = _ModelRepo(existing=preferred)
    preferences = _PreferenceRepo(global_model_id="global-model")
    scope = OwnerScope.personal("user-1")

    result = await _service(repo, preferences).set_preference(
        preferred.id,
        scope=scope,
    )

    assert result.is_default is True
    assert preferences.saved == [(scope, preferred.id)]
    assert repo.saved == []
    assert repo.clear_default_calls == 0
