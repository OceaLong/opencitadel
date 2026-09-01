from sqlalchemy import select

from app.domain.models.scope import OwnerScope
from app.infrastructure.models.skill import SkillORM
from app.infrastructure.repositories.db_skill_repository import DBSkillRepository


def _where_for(scope: OwnerScope, *, global_only: bool = False):
    repo = DBSkillRepository(db_session=None)
    compiled = repo._apply_scope(select(SkillORM), scope, global_only=global_only).compile()
    return str(compiled).split("WHERE", 1)[1], compiled.params


def test_personal_scope_excludes_team_skills():
    where_sql, params = _where_for(OwnerScope.personal("user-1"))

    assert "skills.owner_user_id" in where_sql
    assert "skills.team_id IS NULL" in where_sql
    assert "user-1" in params.values()


def test_team_scope_filters_skills_by_team_not_creator():
    where_sql, params = _where_for(OwnerScope.team("member-2", "team-1"))

    assert "skills.team_id" in where_sql
    assert "skills.owner_user_id" not in where_sql
    assert "team-1" in params.values()
    assert "member-2" not in params.values()


def test_global_only_forces_global_visibility_ignoring_scope():
    # Even with a concrete personal scope, global_only must collapse the filter
    # to visibility == "global" and never leak owner/team-private rows. This is
    # the guard for the unauthenticated A2A agent-card discovery endpoint.
    where_sql, params = _where_for(OwnerScope.personal("user-1"), global_only=True)

    assert "skills.visibility" in where_sql
    assert "global" in params.values()
    assert "skills.owner_user_id" not in where_sql
    assert "skills.team_id" not in where_sql
    assert "user-1" not in params.values()


def test_global_only_with_no_scope_still_filters_global():
    # scope=None normally returns everything; global_only must override that so
    # an anonymous caller can never enumerate all tenants' skills.
    where_sql, params = _where_for(None, global_only=True)

    assert "skills.visibility" in where_sql
    assert "global" in params.values()
