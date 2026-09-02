"""The destructive cutover has one exact greenfield schema authority."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.infrastructure.models.registry import MODEL_MODULES, model_metadata

KERNEL_TABLES = {
    "kernel_runs",
    "kernel_commands",
    "kernel_events",
    "kernel_effects",
    "kernel_timers",
    "kernel_outbox",
    "kernel_run_views",
    "kernel_message_views",
    "kernel_effect_views",
    "kernel_approval_views",
    "kernel_approval_reviewers",
    "kernel_public_events",
    "kernel_resource_build_views",
    "kernel_notification_views",
}
IDENTITY_TABLES = {
    "users",
    "oauth_identities",
    "refresh_tokens",
    "teams",
    "team_members",
    "invitations",
    "user_quotas",
    "team_quotas",
    "audit_records",
    "governance_policy_revisions",
    "governance_policy_head",
}
INFERENCE_TABLES = {
    "inference_endpoints",
    "inference_models",
    "inference_bindings",
    "inference_usage",
    "mcp_servers",
}
KNOWLEDGE_TABLES = {
    "files",
    "artifacts",
    "knowledge_bases",
    "knowledge_versions",
    "knowledge_documents",
    "knowledge_chunks",
}
EXPECTED_TABLES = KERNEL_TABLES | IDENTITY_TABLES | INFERENCE_TABLES | KNOWLEDGE_TABLES


def test_greenfield_schema_is_the_single_root_and_head() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_bases() == ["0001greenfield"]
    assert script.get_heads() == ["0001greenfield"]
    assert sorted(path.name for path in Path("alembic/versions").glob("*.py")) == [
        "0001greenfield_initial.py"
    ]


def test_registry_loads_only_four_context_model_modules() -> None:
    assert MODEL_MODULES == (
        "app.contexts.identity.models",
        "app.contexts.inference.models",
        "app.contexts.knowledge.models",
        "app.kernel.infrastructure.postgres.models",
    )
    assert set(model_metadata.tables) == EXPECTED_TABLES


def test_retired_authorities_are_absent_from_metadata() -> None:
    retired_fragments = {
        "session",
        "execution_",
        "scheduled",
        "patrol",
        "remediation",
        "compliance",
        "a2a",
        "memory",
        "skill",
        "service_api_key",
        "share",
    }
    for table_name in model_metadata.tables:
        assert not any(fragment in table_name for fragment in retired_fragments)


def test_credentials_are_encrypted_and_run_status_lives_only_in_views() -> None:
    endpoint = model_metadata.tables["inference_endpoints"]
    mcp = model_metadata.tables["mcp_servers"]
    assert str(endpoint.c.credential_encryption.server_default.arg) == "'fernet_v2'"
    assert str(mcp.c.secret_encryption.server_default.arg) == "'fernet_v2'"
    assert "status" not in model_metadata.tables["kernel_runs"].c
    assert "status" in model_metadata.tables["kernel_run_views"].c


def test_every_direct_owner_table_enforces_exactly_one_owner() -> None:
    names = {
        "kernel_runs",
        "kernel_commands",
        "kernel_events",
        "kernel_effects",
        "kernel_timers",
        "kernel_run_views",
        "kernel_message_views",
        "kernel_effect_views",
        "kernel_approval_views",
        "kernel_public_events",
        "kernel_resource_build_views",
        "files",
        "artifacts",
        "knowledge_bases",
        "inference_usage",
    }
    for name in names:
        checks = " ".join(
            str(constraint.sqltext)
            for constraint in model_metadata.tables[name].constraints
            if hasattr(constraint, "sqltext")
        )
        assert "owner_user_id IS NOT NULL" in checks
        assert "team_id IS NOT NULL" in checks
