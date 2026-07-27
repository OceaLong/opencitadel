#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest
from sqlalchemy import select

from app.domain.models.scope import OwnerScope
from app.infrastructure.models.llm_model import LLMModelORM
from app.infrastructure.repositories.db_llm_model_repository import DBLLMModelRepository
from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class _FakeRecord:
    def __init__(self, api_key: str, encryption: str):
        self.api_key = api_key
        self.api_key_encryption = encryption


def test_resolve_legacy_plaintext_without_decrypt():
    repo = DBLLMModelRepository(db_session=None, cipher=ApiKeyCipher("d" * 32))
    assert repo._resolve_api_key("sk-plain", ApiKeyEncryption.LEGACY_PLAINTEXT) == "sk-plain"


def test_resolve_fernet_v1_decrypts():
    cipher = ApiKeyCipher("d" * 32)
    repo = DBLLMModelRepository(db_session=None, cipher=cipher)
    encrypted = cipher.encrypt("sk-secret")
    assert repo._resolve_api_key(encrypted, ApiKeyEncryption.FERNET_V1) == "sk-secret"


def test_resolve_fernet_v2_decrypts_with_embedded_key_id():
    cipher = ApiKeyCipher("d" * 32, key_id="key-2")
    repo = DBLLMModelRepository(db_session=None, cipher=cipher)
    encrypted = cipher.encrypt_versioned("sk-secret")

    assert repo._resolve_api_key(encrypted, ApiKeyEncryption.FERNET_V2) == "sk-secret"


def test_resolve_fernet_v1_raises_on_wrong_secret():
    cipher_a = ApiKeyCipher("d" * 32)
    cipher_b = ApiKeyCipher("e" * 32)
    encrypted = cipher_a.encrypt("sk-secret")
    repo = DBLLMModelRepository(db_session=None, cipher=cipher_b)

    with pytest.raises(ApiKeyCipherError):
        repo._resolve_api_key(encrypted, ApiKeyEncryption.FERNET_V1)


class _EmptyResult:
    @staticmethod
    def first():
        return None

    @staticmethod
    def all():
        return []

    @staticmethod
    def scalar():
        return 0


class _CapturingSession:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _EmptyResult()


def _compiled_params(statement):
    compiled = statement.compile()
    return str(compiled), compiled.params


def test_personal_scope_excludes_team_models():
    repo = DBLLMModelRepository(db_session=None, cipher=ApiKeyCipher("d" * 32))

    sql, params = _compiled_params(
        repo._apply_scope(
            select(LLMModelORM),
            OwnerScope.personal("user-1"),
        )
    )

    assert "llm_models.owner_user_id" in sql
    assert "llm_models.team_id IS NULL" in sql
    assert "user-1" in params.values()


def test_team_scope_filters_by_team_id_instead_of_creator():
    repo = DBLLMModelRepository(db_session=None, cipher=ApiKeyCipher("d" * 32))

    sql, params = _compiled_params(
        repo._apply_scope(
            select(LLMModelORM),
            OwnerScope.team("member-2", "team-1"),
        )
    )

    where_sql = sql.split("WHERE", 1)[1]
    assert "llm_models.team_id" in sql
    assert "llm_models.owner_user_id" not in where_sql
    assert "team-1" in params.values()
    assert "member-2" not in params.values()


def test_scoped_model_join_requires_endpoint_in_same_scope():
    repo = DBLLMModelRepository(db_session=None, cipher=ApiKeyCipher("d" * 32))

    sql, params = _compiled_params(
        repo._model_stmt(OwnerScope.team("member-2", "team-1"))
    )

    assert "llm_models.team_id" in sql
    assert "llm_endpoints.team_id" in sql
    assert list(params.values()).count("team-1") == 2


@pytest.mark.asyncio
async def test_default_lookup_only_queries_global_models():
    session = _CapturingSession()
    repo = DBLLMModelRepository(
        db_session=session,
        cipher=ApiKeyCipher("d" * 32),
    )

    assert await repo.get_default() is None

    assert len(session.statements) == 2
    for statement in session.statements:
        sql, params = _compiled_params(statement)
        assert "llm_models.visibility" in sql
        assert "global" in params.values()


@pytest.mark.asyncio
async def test_clear_default_only_updates_global_models():
    session = _CapturingSession()
    repo = DBLLMModelRepository(
        db_session=session,
        cipher=ApiKeyCipher("d" * 32),
    )

    await repo.clear_default()

    sql, params = _compiled_params(session.statements[0])
    assert "llm_models.visibility" in sql
    assert "global" in params.values()


@pytest.mark.asyncio
async def test_global_list_and_count_queries_exclude_private_models():
    session = _CapturingSession()
    repo = DBLLMModelRepository(
        db_session=session,
        cipher=ApiKeyCipher("d" * 32),
    )

    assert await repo.get_all_global() == []
    assert await repo.count_global() == 0

    assert len(session.statements) == 2
    for statement in session.statements:
        sql, params = _compiled_params(statement)
        assert "llm_models.visibility" in sql
        assert "global" in params.values()
