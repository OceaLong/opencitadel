#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import BadRequestError, ForbiddenError
from app.application.services.skill_service import SkillService
from app.domain.models.scope import OwnerScope
from app.domain.models.skill import ResourceVisibility, Skill


class _SkillRepo:
    def __init__(self, existing=None) -> None:
        self.saved = None
        self.existing = existing

    async def get_by_slug(self, slug):
        return None

    async def save(self, skill):
        self.saved = skill

    async def get_by_id(self, _skill_id, scope=None):
        return self.existing


class _Uow:
    def __init__(self, repo):
        self.skill = repo
        self.llm_model = _DeniedModelRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _DeniedModelRepo:
    async def get_by_id(self, _model_id, scope=None):
        return None


@pytest.mark.asyncio
async def test_create_private_skill_binds_team_scope():
    repo = _SkillRepo()
    service = SkillService(lambda: _Uow(repo))

    await service.create_skill(
        Skill(
            name="Team skill",
            visibility=ResourceVisibility.PRIVATE,
        ),
        scope=OwnerScope.team("creator-1", "team-1"),
    )

    assert repo.saved.owner_user_id == "creator-1"
    assert repo.saved.team_id == "team-1"


@pytest.mark.asyncio
async def test_create_skill_rejects_cross_scope_recommended_model():
    repo = _SkillRepo()
    service = SkillService(lambda: _Uow(repo))

    with pytest.raises(BadRequestError, match="推荐模型"):
        await service.create_skill(
            Skill(
                name="Team skill",
                recommended_model_id="other-team-model",
                visibility=ResourceVisibility.PRIVATE,
            ),
            scope=OwnerScope.team("creator-1", "team-1"),
        )

    assert repo.saved is None


@pytest.mark.asyncio
async def test_update_skill_rejects_cross_scope_recommended_model():
    existing = Skill(
        id="skill-1",
        name="Team skill",
        slug="team-skill",
        owner_user_id="creator-1",
        team_id="team-1",
        visibility=ResourceVisibility.PRIVATE,
    )
    repo = _SkillRepo(existing)
    service = SkillService(lambda: _Uow(repo))

    with pytest.raises(BadRequestError, match="推荐模型"):
        await service.update_skill(
            "skill-1",
            Skill(
                name="Team skill",
                slug="team-skill",
                recommended_model_id="other-team-model",
                visibility=ResourceVisibility.PRIVATE,
            ),
            scope=OwnerScope.team("creator-1", "team-1"),
        )

    assert repo.saved is None


@pytest.mark.asyncio
async def test_create_global_skill_requires_explicit_admin_capability():
    repo = _SkillRepo()
    service = SkillService(lambda: _Uow(repo))

    with pytest.raises(ForbiddenError):
        await service.create_skill(
            Skill(name="Global skill", visibility=ResourceVisibility.GLOBAL),
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved is None


@pytest.mark.asyncio
async def test_update_private_skill_cannot_escalate_to_global():
    existing = Skill(
        id="skill-1",
        name="Private skill",
        slug="private-skill",
        owner_user_id="user-1",
        visibility=ResourceVisibility.PRIVATE,
    )
    repo = _SkillRepo(existing)
    service = SkillService(lambda: _Uow(repo))

    with pytest.raises(ForbiddenError):
        await service.update_skill(
            existing.id,
            existing.model_copy(update={"visibility": ResourceVisibility.GLOBAL}),
            scope=OwnerScope.personal("user-1"),
        )

    assert repo.saved is None
