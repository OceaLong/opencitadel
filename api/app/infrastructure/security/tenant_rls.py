"""PostgreSQL row-level-security policy builders for workspace-owned data."""

from __future__ import annotations

import re

PRIVATE_ROOT_TABLES = {
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "codebases",
    "files",
    "llm_token_usages",
    "scheduled_jobs",
    "patrol_packs",
}

VISIBILITY_ROOT_TABLES = {
    "inference_models",
    "inference_endpoints",
    "skills",
    "mcp_servers",
    "a2a_servers",
}

POLICY_ROOT_TABLES = {
    "execution_policy_revisions",
    "operations_policy_revisions",
    "runtime_policy_heads",
}

EXECUTION_ROOT_TABLES = {
    "execution_stream_owners",
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
}

CHILD_TABLES = {
    "session_file_attachments": ("sessions", "session_id", "id"),
    "session_resource_bindings": ("sessions", "session_id", "id"),
    "artifacts": ("sessions", "session_id", "id"),
    "codebase_versions": ("codebases", "codebase_id", "id"),
    "codebase_files": ("codebases", "codebase_id", "id"),
    "codebase_symbols": ("codebases", "codebase_id", "id"),
    "codebase_edges": ("codebases", "codebase_id", "id"),
    "codebase_chunks": ("codebases", "codebase_id", "id"),
    "codebase_artifacts": ("codebases", "codebase_id", "id"),
    "knowledge_documents": ("knowledge_bases", "kb_id", "id"),
    "knowledge_base_versions": (
        "knowledge_bases",
        "knowledge_base_id",
        "id",
    ),
    "knowledge_base_version_documents": (
        "knowledge_bases",
        "knowledge_base_id",
        "id",
    ),
    "knowledge_document_revisions": (
        "knowledge_documents",
        "document_id",
        "id",
    ),
    "knowledge_chunks": ("knowledge_bases", "kb_id", "id"),
    "knowledge_entities": ("knowledge_bases", "kb_id", "id"),
    "knowledge_relations": ("knowledge_bases", "kb_id", "id"),
    "knowledge_entity_refs": ("knowledge_bases", "kb_id", "id"),
    "patrol_runs": ("patrol_packs", "pack_id", "id"),
    "patrol_check_results": ("patrol_runs", "run_id", "id"),
    "patrol_findings": ("patrol_runs", "run_id", "id"),
    "patrol_remediations": ("patrol_runs", "run_id", "id"),
}

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_AUTH_MODE = "COALESCE(current_setting('app.auth_mode', true), 'anonymous')"
_USER_ID = "NULLIF(current_setting('app.user_id', true), '')"
_TEAM_ID = "NULLIF(current_setting('app.team_id', true), '')"
_IS_ADMIN = "COALESCE(current_setting('app.is_admin', true), 'false') = 'true'"
_AUTHORIZATION_VALID = "opencitadel_authorization_valid()"


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value}")
    return value


def _system_or_admin() -> str:
    return f"({_AUTH_MODE} = 'system' OR {_IS_ADMIN})"


def _workspace_owner_predicate() -> str:
    return (
        "("
        f"({_TEAM_ID} IS NOT NULL AND team_id = {_TEAM_ID})"
        " OR "
        f"({_TEAM_ID} IS NULL AND team_id IS NULL AND owner_user_id = {_USER_ID})"
        ")"
    )


def direct_select_predicate(*, has_visibility: bool) -> str:
    visible = "visibility = 'global' OR " if has_visibility else ""
    return (
        f"{_system_or_admin()} OR "
        f"({_AUTH_MODE} = 'user' AND ({visible}{_workspace_owner_predicate()}))"
    )


def direct_write_predicate(*, has_visibility: bool) -> str:
    private_only = "visibility <> 'global' AND " if has_visibility else ""
    return (
        f"{_system_or_admin()} OR "
        f"({_AUTH_MODE} = 'user' AND {private_only}{_workspace_owner_predicate()})"
    )


def policy_control_plane_predicate() -> str:
    return _system_or_admin()


def preference_select_predicate() -> str:
    return (
        f"{_system_or_admin()} OR "
        f"({_AUTH_MODE} = 'user' AND ("
        "scope_type = 'global'"
        f" OR (scope_type = 'user' AND owner_user_id = {_USER_ID})"
        f" OR (scope_type = 'team' AND team_id = {_TEAM_ID})"
        "))"
    )


def preference_write_predicate() -> str:
    return (
        f"{_system_or_admin()} OR "
        f"({_AUTH_MODE} = 'user' AND ("
        f"(scope_type = 'user' AND owner_user_id = {_USER_ID})"
        f" OR (scope_type = 'team' AND {_TEAM_ID} IS NOT NULL AND team_id = {_TEAM_ID})"
        "))"
    )


def child_predicate(
    table_name: str,
    parent_table: str,
    child_foreign_key: str,
    parent_key: str,
) -> str:
    table = _identifier(table_name)
    parent = _identifier(parent_table)
    child_fk = _identifier(child_foreign_key)
    parent_pk = _identifier(parent_key)
    return f"EXISTS (SELECT 1 FROM {parent} WHERE {parent}.{parent_pk} = {table}.{child_fk})"


def policy_statements(
    table_name: str,
    *,
    has_visibility: bool = False,
    inherited_predicate: str | None = None,
    select_predicate: str | None = None,
    write_predicate: str | None = None,
) -> list[str]:
    table = _identifier(table_name)
    resolved_select = (
        select_predicate
        or inherited_predicate
        or direct_select_predicate(has_visibility=has_visibility)
    )
    resolved_write = (
        write_predicate
        or inherited_predicate
        or direct_write_predicate(has_visibility=has_visibility)
    )
    resolved_select = f"{_AUTHORIZATION_VALID} AND ({resolved_select})"
    resolved_write = f"{_AUTHORIZATION_VALID} AND ({resolved_write})"
    prefix = f"{table}_tenant"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {prefix}_select ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_insert ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_update ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_delete ON {table}",
        (f"CREATE POLICY {prefix}_select ON {table} FOR SELECT USING ({resolved_select})"),
        (f"CREATE POLICY {prefix}_insert ON {table} FOR INSERT WITH CHECK ({resolved_write})"),
        (
            f"CREATE POLICY {prefix}_update ON {table} FOR UPDATE "
            f"USING ({resolved_write}) WITH CHECK ({resolved_write})"
        ),
        (f"CREATE POLICY {prefix}_delete ON {table} FOR DELETE USING ({resolved_write})"),
    ]


def disable_policy_statements(table_name: str) -> list[str]:
    table = _identifier(table_name)
    prefix = f"{table}_tenant"
    return [
        f"DROP POLICY IF EXISTS {prefix}_select ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_insert ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_update ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_delete ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]
