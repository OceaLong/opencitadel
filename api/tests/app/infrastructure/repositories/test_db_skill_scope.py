from sqlalchemy import select

from app.domain.models.scope import OwnerScope
from app.infrastructure.models.skill import SkillORM
from app.infrastructure.repositories.db_skill_repository import DBSkillRepository


def _where_for(scope: OwnerScope):
    repo = DBSkillRepository(db_session=None)
    compiled = repo._apply_scope(select(SkillORM), scope).compile()
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
