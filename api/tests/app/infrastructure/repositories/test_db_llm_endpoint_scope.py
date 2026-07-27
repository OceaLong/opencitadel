#!/usr/bin/env python
# -*- coding: utf-8 -*-
from sqlalchemy import select

from app.domain.models.scope import OwnerScope
from app.infrastructure.models.llm_endpoint import LLMEndpointORM
from app.infrastructure.repositories.db_llm_endpoint_repository import DBLLMEndpointRepository
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


def _compiled(statement):
    compiled = statement.compile()
    return str(compiled), compiled.params


def test_personal_scope_excludes_team_endpoints():
    repo = DBLLMEndpointRepository(
        db_session=None,
        cipher=ApiKeyCipher("e" * 32),
    )

    sql, params = _compiled(
        repo._apply_scope(
            select(LLMEndpointORM),
            OwnerScope.personal("user-1"),
        )
    )

    assert "llm_endpoints.owner_user_id" in sql
    assert "llm_endpoints.team_id IS NULL" in sql
    assert "user-1" in params.values()


def test_team_scope_filters_by_team_id_instead_of_creator():
    repo = DBLLMEndpointRepository(
        db_session=None,
        cipher=ApiKeyCipher("e" * 32),
    )

    sql, params = _compiled(
        repo._apply_scope(
            select(LLMEndpointORM),
            OwnerScope.team("member-2", "team-1"),
        )
    )

    where_sql = sql.split("WHERE", 1)[1]
    assert "llm_endpoints.team_id" in sql
    assert "llm_endpoints.owner_user_id" not in where_sql
    assert "team-1" in params.values()
    assert "member-2" not in params.values()
