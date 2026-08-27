from app.infrastructure.security import tenant_rls
from app.infrastructure.security.tenant_rls import (
    CHILD_TABLES,
    EXECUTION_ROOT_TABLES,
    PRIVATE_ROOT_TABLES,
    VISIBILITY_ROOT_TABLES,
    direct_select_predicate,
    direct_write_predicate,
    policy_statements,
    preference_select_predicate,
    preference_write_predicate,
)


def test_visibility_select_requires_authenticated_context_for_global_rows():
    predicate = direct_select_predicate(has_visibility=True)

    assert "app.auth_mode" in predicate
    assert "visibility = 'global'" in predicate
    assert "anonymous" in predicate


def test_visibility_write_does_not_grant_global_mutation_to_regular_user():
    predicate = direct_write_predicate(has_visibility=True)

    assert "visibility <> 'global'" in predicate
    assert "app.is_admin" in predicate
    assert "app.auth_mode" in predicate


def test_policy_control_plane_requires_system_or_admin_authorization():
    predicate = tenant_rls.policy_control_plane_predicate()

    assert "app.auth_mode" in predicate
    assert "'system'" in predicate
    assert "app.is_admin" in predicate
    assert "app.user_id" not in predicate
    assert "app.team_id" not in predicate


def test_model_preference_policy_separates_global_user_and_team_bindings():
    select_predicate = preference_select_predicate()
    write_predicate = preference_write_predicate()

    assert "scope_type = 'global'" in select_predicate
    assert "scope_type = 'user'" in select_predicate
    assert "scope_type = 'team'" in select_predicate
    assert "scope_type = 'global'" not in write_predicate
    assert "app.is_admin" in write_predicate


def test_rls_policy_matrix_covers_root_and_child_tenant_tables():
    root_tables = (
        PRIVATE_ROOT_TABLES
        | VISIBILITY_ROOT_TABLES
        | tenant_rls.POLICY_ROOT_TABLES
        | EXECUTION_ROOT_TABLES
    )

    assert {
        "sessions",
        "memory_entries",
        "knowledge_bases",
        "codebases",
        "files",
        "llm_token_usages",
        "inference_models",
        "inference_endpoints",
        "skills",
        "mcp_servers",
        "a2a_servers",
        "scheduled_jobs",
        "execution_policy_revisions",
        "operations_policy_revisions",
        "runtime_policy_heads",
        "execution_events",
        "execution_command_inbox",
        "execution_outbox",
        "execution_scheduled_commands",
        "execution_activity_tasks",
        "execution_snapshots",
        "execution_projector_checkpoints",
        "execution_run_projection",
        "execution_resource_build_projection",
        "execution_public_events",
        "execution_activity_projection",
        "execution_approval_projection",
    } <= root_tables
    assert {
        "session_file_attachments",
        "artifacts",
        "codebase_files",
        "codebase_symbols",
        "codebase_edges",
        "codebase_chunks",
        "codebase_artifacts",
        "knowledge_documents",
        "knowledge_chunks",
        "knowledge_entities",
        "knowledge_relations",
    } <= set(CHILD_TABLES)

    statements = policy_statements("inference_models", has_visibility=True)
    assert any("FORCE ROW LEVEL SECURITY" in statement for statement in statements)
    assert any("FOR SELECT" in statement for statement in statements)
    assert any("FOR INSERT" in statement for statement in statements)
    assert any("FOR UPDATE" in statement for statement in statements)
    assert any("FOR DELETE" in statement for statement in statements)
    assert all(
        "opencitadel_authorization_valid()" in statement
        for statement in statements
        if "CREATE POLICY" in statement
    )
