#!/usr/bin/env python
# -*- coding: utf-8 -*-
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
}

VISIBILITY_ROOT_TABLES = {
    "llm_models",
    "llm_endpoints",
    "skills",
    "mcp_servers",
    "a2a_servers",
}

CONFIG_ROOT_TABLES = {
    "app_configs",
    "app_config_revisions",
}

CHILD_TABLES = {
    "session_events": ("sessions", "session_id", "id"),
    "session_checkpoints": ("sessions", "session_id", "id"),
    "session_agent_memories": ("sessions", "session_id", "id"),
    "session_file_attachments": ("sessions", "session_id", "id"),
    "artifacts": ("sessions", "session_id", "id"),
    "codebase_files": ("codebases", "codebase_id", "id"),
    "codebase_symbols": ("codebases", "codebase_id", "id"),
    "codebase_edges": ("codebases", "codebase_id", "id"),
    "codebase_chunks": ("codebases", "codebase_id", "id"),
    "codebase_artifacts": ("codebases", "codebase_id", "id"),
    "knowledge_documents": ("knowledge_bases", "kb_id", "id"),
    "knowledge_chunks": ("knowledge_bases", "kb_id", "id"),
    "knowledge_entities": ("knowledge_bases", "kb_id", "id"),
    "knowledge_relations": ("knowledge_bases", "kb_id", "id"),
}

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_AUTH_MODE = "COALESCE(current_setting('app.auth_mode', true), 'anonymous')"
_USER_ID = "NULLIF(current_setting('app.user_id', true), '')"
_TEAM_ID = "NULLIF(current_setting('app.team_id', true), '')"
_IS_ADMIN = "COALESCE(current_setting('app.is_admin', true), 'false') = 'true'"


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


def config_select_predicate() -> str:
    return (
        f"{_system_or_admin()} OR "
        f"({_AUTH_MODE} = 'user' AND ("
        "scope = 'global' OR "
        f"(scope = 'user' AND owner_user_id = {_USER_ID})"
        "))"
    )


def config_write_predicate() -> str:
    return (
        f"{_system_or_admin()} OR "
        f"({_AUTH_MODE} = 'user' AND "
        f"scope = 'user' AND owner_user_id = {_USER_ID})"
    )


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
    return (
        f"EXISTS (SELECT 1 FROM {parent} "
        f"WHERE {parent}.{parent_pk} = {table}.{child_fk})"
    )


def policy_statements(
    table_name: str,
    *,
    has_visibility: bool = False,
    inherited_predicate: str | None = None,
    select_predicate: str | None = None,
    write_predicate: str | None = None,
) -> list[str]:
    table = _identifier(table_name)
    resolved_select = select_predicate or inherited_predicate or direct_select_predicate(
        has_visibility=has_visibility
    )
    resolved_write = write_predicate or inherited_predicate or direct_write_predicate(
        has_visibility=has_visibility
    )
    prefix = f"{table}_tenant"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {prefix}_select ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_insert ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_update ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_delete ON {table}",
        (
            f"CREATE POLICY {prefix}_select ON {table} FOR SELECT "
            f"USING ({resolved_select})"
        ),
        (
            f"CREATE POLICY {prefix}_insert ON {table} FOR INSERT "
            f"WITH CHECK ({resolved_write})"
        ),
        (
            f"CREATE POLICY {prefix}_update ON {table} FOR UPDATE "
            f"USING ({resolved_write}) WITH CHECK ({resolved_write})"
        ),
        (
            f"CREATE POLICY {prefix}_delete ON {table} FOR DELETE "
            f"USING ({resolved_write})"
        ),
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
