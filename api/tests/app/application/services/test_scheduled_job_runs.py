"""Unit proof for ScheduledJobService.list_runs (E10b) ownership + wiring."""

from types import SimpleNamespace

import pytest

from app.application.services.scheduled_job_service import ScheduledJobService
from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope
from tests.runtime_policy_support import MutablePolicyReader

_SECRET_CIPHER = SimpleNamespace(
    current_key_id="test",
    encrypt_versioned=lambda value: f"encrypted:{value}",
    decrypt_versioned=lambda value: value.removeprefix("encrypted:"),
)


class _JobRepo:
    def __init__(self, existing=None) -> None:
        self.existing = existing

    async def get_by_id(self, _job_id, scope=None, for_update=False):
        return self.existing


class _Uow:
    def __init__(self, repo):
        self.scheduled_job = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        return None


class _RecordingProjection:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_runs_for_source(self, **kwargs):
        self.calls.append(kwargs)
        return ("run-entry",)


def _service(repo, projection) -> ScheduledJobService:
    return ScheduledJobService(
        lambda: _Uow(repo),
        patrol_run_service=SimpleNamespace(),
        resource_guard=SimpleNamespace(),
        resource_binding_service=SimpleNamespace(),
        run_admission_service=SimpleNamespace(),
        run_projection=projection,
        policy_reader=MutablePolicyReader(),
        notification_service=SimpleNamespace(),
        secret_cipher=_SECRET_CIPHER,
    )


@pytest.mark.asyncio
async def test_list_runs_returns_none_for_missing_or_out_of_scope_job():
    projection = _RecordingProjection()
    service = _service(_JobRepo(existing=None), projection)

    result = await service.list_runs("job-x", OwnerScope.personal("user-1"))

    assert result is None
    # No projection read is attempted when the scoped job lookup misses.
    assert projection.calls == []


@pytest.mark.asyncio
async def test_list_runs_queries_projection_by_scheduled_job_association_key():
    job = ScheduledJob(
        id="job-1",
        name="Nightly",
        owner_user_id="creator-1",
        team_id="team-1",
        trigger_type="interval",
        trigger_spec="3600",
        prompt_template="run",
    )
    projection = _RecordingProjection()
    service = _service(_JobRepo(existing=job), projection)

    result = await service.list_runs(
        "job-1",
        OwnerScope.team("creator-1", "team-1"),
        limit=10,
        offset=5,
    )

    assert result == ("run-entry",)
    assert projection.calls == [
        {
            "source_entity_type": "scheduled_job",
            "source_entity_id": "job-1",
            "owner_scope": OwnerScope.team("creator-1", "team-1"),
            "limit": 10,
            "offset": 5,
        }
    ]
