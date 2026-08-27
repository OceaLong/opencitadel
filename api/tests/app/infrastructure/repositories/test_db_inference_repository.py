from sqlalchemy import select

from app.domain.models.inference import InferencePurpose
from app.domain.models.scope import OwnerScope
from app.infrastructure.models.inference_binding import InferenceBindingORM
from app.infrastructure.models.inference_endpoint import InferenceEndpointORM
from app.infrastructure.models.inference_model import InferenceModelORM
from app.infrastructure.repositories.db_inference_binding_repository import (
    binding_identity,
)
from app.infrastructure.repositories.db_inference_endpoint_repository import (
    DBInferenceEndpointRepository,
)
from app.infrastructure.repositories.db_inference_model_repository import (
    DBInferenceModelRepository,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


def _compiled(statement):
    compiled = statement.compile()
    return str(compiled), compiled.params


def test_inference_tables_replace_llm_resource_tables() -> None:
    assert InferenceEndpointORM.__tablename__ == "inference_endpoints"
    assert InferenceModelORM.__tablename__ == "inference_models"
    assert InferenceBindingORM.__tablename__ == "inference_bindings"


def test_model_persistence_does_not_duplicate_endpoint_connection_or_secret() -> None:
    columns = set(InferenceModelORM.__table__.c.keys())
    assert {"provider", "base_url", "credential", "api_key"}.isdisjoint(columns)
    assert {"endpoint_id", "kind", "settings", "capabilities"} <= columns


def test_endpoint_credential_uses_only_current_encryption_marker() -> None:
    columns = InferenceEndpointORM.__table__.c
    assert "credential" in columns
    assert "credential_encryption" in columns
    assert str(columns.credential_encryption.server_default.arg) == "'fernet_v2'"


def test_binding_identity_is_unique_per_scope_and_purpose() -> None:
    assert binding_identity(None, InferencePurpose.CHAT) == (
        "global:global:chat",
        "global",
        "global",
        None,
        None,
    )
    assert binding_identity(OwnerScope.personal("user-1"), InferencePurpose.EMBEDDING) == (
        "user:user-1:embedding",
        "user",
        "user-1",
        "user-1",
        None,
    )
    assert binding_identity(OwnerScope.team("member-1", "team-1"), InferencePurpose.RERANK) == (
        "team:team-1:rerank",
        "team",
        "team-1",
        None,
        "team-1",
    )


def test_personal_scope_excludes_team_inference_resources() -> None:
    endpoint_repo = DBInferenceEndpointRepository(
        db_session=None,
        cipher=ApiKeyCipher("e" * 32),
    )
    model_repo = DBInferenceModelRepository(db_session=None)

    endpoint_sql, endpoint_params = _compiled(
        endpoint_repo._apply_scope(
            select(InferenceEndpointORM),
            OwnerScope.personal("user-1"),
        )
    )
    model_sql, model_params = _compiled(
        model_repo._apply_scope(
            select(InferenceModelORM),
            OwnerScope.personal("user-1"),
        )
    )

    assert "inference_endpoints.team_id IS NULL" in endpoint_sql
    assert "inference_models.team_id IS NULL" in model_sql
    assert "user-1" in endpoint_params.values()
    assert "user-1" in model_params.values()


def test_team_scope_filters_by_team_not_creator() -> None:
    model_repo = DBInferenceModelRepository(db_session=None)
    sql, params = _compiled(
        model_repo._apply_scope(
            select(InferenceModelORM),
            OwnerScope.team("member-2", "team-1"),
        )
    )

    where_sql = sql.split("WHERE", 1)[1]
    assert "inference_models.team_id" in where_sql
    assert "inference_models.owner_user_id" not in where_sql
    assert "team-1" in params.values()
    assert "member-2" not in params.values()
