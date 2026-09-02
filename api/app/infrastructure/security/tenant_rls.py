"""Single RLS catalog for the greenfield schema."""

from __future__ import annotations

import re

DIRECT_OWNER_TABLES = frozenset(
    {
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
        "inference_usage",
        "files",
        "artifacts",
        "knowledge_bases",
    }
)
VISIBILITY_TABLES = frozenset({"inference_endpoints", "inference_models", "mcp_servers"})
CHILD_TABLES = {
    "kernel_approval_reviewers": ("kernel_approval_views", "approval_id", "id"),
    "knowledge_versions": ("knowledge_bases", "knowledge_base_id", "id"),
    "knowledge_documents": ("knowledge_bases", "knowledge_base_id", "id"),
    "knowledge_chunks": ("knowledge_bases", "knowledge_base_id", "id"),
}
SYSTEM_TABLES = frozenset(
    {
        "kernel_outbox",
        "audit_records",
        "governance_policy_revisions",
        "governance_policy_head",
    }
)
RLS_TABLES = (
    DIRECT_OWNER_TABLES
    | VISIBILITY_TABLES
    | frozenset(CHILD_TABLES)
    | SYSTEM_TABLES
    | {
        "users",
        "oauth_identities",
        "refresh_tokens",
        "teams",
        "team_members",
        "invitations",
        "user_quotas",
        "team_quotas",
        "inference_bindings",
        "kernel_notification_views",
    }
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_VALID = "opencitadel_authorization_valid()"
_MODE = "COALESCE(current_setting('app.auth_mode', true), 'anonymous')"
_USER = "NULLIF(current_setting('app.user_id', true), '')"
_TEAM = "NULLIF(current_setting('app.team_id', true), '')"
_ADMIN = "COALESCE(current_setting('app.is_admin', true), 'false') = 'true'"
_AUDITOR = "COALESCE(current_setting('app.is_auditor', true), 'false') = 'true'"


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value}")
    return value


def _privileged() -> str:
    return f"({_MODE} = 'system' OR {_ADMIN})"


def _direct_owner() -> str:
    return (
        f"({_MODE} = 'user' AND ((team_id IS NOT NULL AND team_id = {_TEAM}) OR "
        f"(team_id IS NULL AND owner_user_id = {_USER})))"
    )


def _policy_statements(
    table_name: str,
    *,
    select_predicate: str,
    write_predicate: str,
) -> list[str]:
    table = _identifier(table_name)
    select = f"{_VALID} AND ({_AUDITOR} OR ({select_predicate}))"
    write = f"{_VALID} AND (NOT {_AUDITOR}) AND ({write_predicate})"
    prefix = f"{table}_tenant"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {prefix}_select ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_insert ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_update ON {table}",
        f"DROP POLICY IF EXISTS {prefix}_delete ON {table}",
        f"CREATE POLICY {prefix}_select ON {table} FOR SELECT USING ({select})",
        f"CREATE POLICY {prefix}_insert ON {table} FOR INSERT WITH CHECK ({write})",
        f"CREATE POLICY {prefix}_update ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write})",
        f"CREATE POLICY {prefix}_delete ON {table} FOR DELETE USING ({write})",
    ]


def _child(table: str, parent: str, foreign_key: str, parent_key: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM {_identifier(parent)} WHERE "
        f"{_identifier(parent)}.{_identifier(parent_key)} = "
        f"{_identifier(table)}.{_identifier(foreign_key)})"
    )


def apply_row_level_security(execute) -> None:
    """Install the complete policy matrix through a list-of-SQL callback."""

    for table in sorted(DIRECT_OWNER_TABLES):
        execute(
            _policy_statements(
                table,
                select_predicate=f"{_privileged()} OR {_direct_owner()}",
                write_predicate=f"{_privileged()} OR {_direct_owner()}",
            )
        )
    for table in sorted(VISIBILITY_TABLES):
        execute(
            _policy_statements(
                table,
                select_predicate=f"{_privileged()} OR visibility = 'global' OR {_direct_owner()}",
                write_predicate=(
                    f"{_privileged()} OR (visibility = 'private' AND {_direct_owner()})"
                ),
            )
        )
    for table, (parent, foreign_key, parent_key) in sorted(CHILD_TABLES.items()):
        inherited = _child(table, parent, foreign_key, parent_key)
        execute(
            _policy_statements(
                table,
                select_predicate=f"{_privileged()} OR {inherited}",
                write_predicate=f"{_privileged()} OR {inherited}",
            )
        )
    for table in sorted(SYSTEM_TABLES):
        execute(
            _policy_statements(
                table,
                select_predicate=_privileged(),
                write_predicate=_privileged(),
            )
        )

    self_row = f"{_privileged()} OR id = {_USER}"
    execute(_policy_statements("users", select_predicate=self_row, write_predicate=_privileged()))
    for table in ("oauth_identities", "refresh_tokens", "user_quotas"):
        own = f"{_privileged()} OR user_id = {_USER}"
        execute(_policy_statements(table, select_predicate=own, write_predicate=_privileged()))
    membership = (
        "EXISTS (SELECT 1 FROM team_members WHERE team_members.team_id = teams.id "
        f"AND team_members.user_id = {_USER})"
    )
    execute(
        _policy_statements(
            "teams",
            select_predicate=f"{_privileged()} OR {membership}",
            write_predicate=_privileged(),
        )
    )
    member_own = f"{_privileged()} OR user_id = {_USER}"
    execute(
        _policy_statements(
            "team_members", select_predicate=member_own, write_predicate=_privileged()
        )
    )
    team_visible = f"{_privileged()} OR team_id = {_TEAM}"
    execute(
        _policy_statements(
            "invitations", select_predicate=team_visible, write_predicate=team_visible
        )
    )
    execute(
        _policy_statements(
            "team_quotas", select_predicate=team_visible, write_predicate=_privileged()
        )
    )
    binding_visible = (
        f"{_privileged()} OR scope_type = 'global' OR "
        f"(scope_type = 'user' AND owner_user_id = {_USER}) OR "
        f"(scope_type = 'team' AND team_id = {_TEAM})"
    )
    execute(
        _policy_statements(
            "inference_bindings",
            select_predicate=binding_visible,
            write_predicate=binding_visible,
        )
    )
    notification_own = f"{_privileged()} OR user_id = {_USER}"
    execute(
        _policy_statements(
            "kernel_notification_views",
            select_predicate=notification_own,
            write_predicate=notification_own,
        )
    )


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


__all__ = [
    "CHILD_TABLES",
    "DIRECT_OWNER_TABLES",
    "RLS_TABLES",
    "SYSTEM_TABLES",
    "VISIBILITY_TABLES",
    "apply_row_level_security",
    "disable_policy_statements",
]
