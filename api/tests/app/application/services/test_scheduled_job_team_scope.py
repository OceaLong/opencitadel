#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.errors import BadRequestError
from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.models.scope import OwnerScope


class _JobRepo:
    def __init__(self, existing=None) -> None:
        self.saved = None
        self.existing = existing

    async def get_by_id(self, _job_id, scope=None):
        return self.existing

    async def save(self, job):
        self.saved = job


class _Uow:
    def __init__(self, repo):
        self.scheduled_job = repo
        self.llm_model = _DeniedRepo()
        self.skill = _DeniedRepo()
        self.codebase = _DeniedRepo()
        self.knowledge_base = _DeniedRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        return None


class _DeniedRepo:
    async def get_by_id(self, _resource_id, scope=None):
        return None

    async def get_kb(self, _resource_id, scope=None):
        return None


@pytest.mark.asyncio
async def test_create_job_binds_team_scope():
    repo = _JobRepo()
    service = ScheduledJobService(lambda: _Uow(repo))

    job, _ = await service.create_job(
        owner_user_id="creator-1",
        name="Team job",
        trigger_type="interval",
        trigger_spec="3600",
        prompt_template="run",
        scope=OwnerScope.team("creator-1", "team-1"),
    )

    assert job.team_id == "team-1"
    assert repo.saved.team_id == "team-1"


@pytest.mark.asyncio
async def test_create_job_rejects_cross_scope_model_reference():
    repo = _JobRepo()
    service = ScheduledJobService(lambda: _Uow(repo))

    with pytest.raises(BadRequestError, match="模型"):
        await service.create_job(
            owner_user_id="creator-1",
            name="Team job",
            trigger_type="interval",
            trigger_spec="3600",
            prompt_template="run",
            model_id="other-team-model",
            scope=OwnerScope.team("creator-1", "team-1"),
        )

    assert repo.saved is None


@pytest.mark.asyncio
async def test_patch_job_rejects_cross_scope_model_reference():
    from app.domain.models.scheduled_job import ScheduledJob

    existing = ScheduledJob(
        id="job-1",
        name="Team job",
        owner_user_id="creator-1",
        team_id="team-1",
        trigger_type="interval",
        trigger_spec="3600",
        prompt_template="run",
    )
    repo = _JobRepo(existing)
    service = ScheduledJobService(lambda: _Uow(repo))

    with pytest.raises(BadRequestError, match="模型"):
        await service.patch_job(
            "job-1",
            OwnerScope.team("creator-1", "team-1"),
            model_id="other-team-model",
        )

    assert repo.saved is None
