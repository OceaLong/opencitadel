#!/usr/bin/env python
# -*- coding: utf-8 -*-
from sqlalchemy import select

from app.domain.models.scope import OwnerScope
from app.infrastructure.models.scheduled_job import ScheduledJobModel
from app.infrastructure.repositories.db_scheduled_job_repository import DBScheduledJobRepository


def test_personal_scope_excludes_team_jobs():
    repo = DBScheduledJobRepository(db_session=None)
    compiled = repo._apply_scope(
        select(ScheduledJobModel),
        OwnerScope.personal("user-1"),
    ).compile()
    where_sql = str(compiled).split("WHERE", 1)[1]

    assert "scheduled_jobs.owner_user_id" in where_sql
    assert "scheduled_jobs.team_id IS NULL" in where_sql
    assert "user-1" in compiled.params.values()


def test_team_scope_filters_jobs_by_team_not_creator():
    repo = DBScheduledJobRepository(db_session=None)
    compiled = repo._apply_scope(
        select(ScheduledJobModel),
        OwnerScope.team("member-2", "team-1"),
    ).compile()
    where_sql = str(compiled).split("WHERE", 1)[1]

    assert "scheduled_jobs.team_id" in where_sql
    assert "scheduled_jobs.owner_user_id" not in where_sql
    assert "team-1" in compiled.params.values()
    assert "member-2" not in compiled.params.values()
