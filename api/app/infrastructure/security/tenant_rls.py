"""PostgreSQL row-level-security policy builders for workspace-owned data."""

from __future__ import annotations

import re

PRIVATE_ROOT_TABLES = {
    "sessions",
    "memory_entries",
    "knowledge_bases",
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
_IS_AUDITOR = "COALESCE(current_setting('app.is_auditor', true), 'false') = 'true'"
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
    # An auditor (read-only global role) may SELECT across every owner for
    # compliance/evidence, but must never satisfy any write predicate. These
    # wraps are uniform and central so no per-table predicate can accidentally
    # omit the guarantee. Only auditors carry is_auditor=true, so `NOT
    # is_auditor` is a no-op for system/admin/regular users.
    resolved_select = f"{_IS_AUDITOR} OR ({resolved_select})"
    resolved_write = f"(NOT {_IS_AUDITOR}) AND ({resolved_write})"
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


_CUSTOM_OWNER_TABLES = {
    "notifications": "user_id",
    "service_api_keys": "owner_user_id",
    "user_quotas": "user_id",
    # Identity credential tables: a principal may only ever see and mutate their
    # own OAuth links and refresh tokens; every privileged auth/session flow that
    # touches another user's rows runs under a system authorization scope.
    "oauth_identities": "user_id",
    "refresh_tokens": "user_id",
}


def _custom_owner_predicate(column: str) -> str:
    return f"({_system_or_admin()}) OR {_identifier(column)} = {_USER_ID}"


def users_select_predicate() -> str:
    """A user sees only themselves (and the system/admin scope).

    Same-team peer visibility deliberately lives in the service layer, not here:
    ``users`` cannot reference ``team_members`` in an RLS predicate without
    dragging ``team_members``' own policy into the check, and because every
    runtime role is ``NOBYPASSRLS`` that self-referential read recurses
    infinitely. Flows that legitimately read other members' user rows (team
    member listings) run under a system authorization scope instead.
    """
    return f"{_system_or_admin()} OR id = {_USER_ID}"


def teams_select_predicate() -> str:
    """A team is visible to its members (and system/admin).

    The membership probe reads ``team_members`` (a different relation), which is
    subject only to its own non-recursive policy, so this stays cycle-free.
    """
    return (
        f"{_system_or_admin()} "
        "OR EXISTS (SELECT 1 FROM team_members "
        f"WHERE team_members.team_id = teams.id AND team_members.user_id = {_USER_ID})"
    )


def team_quotas_select_predicate() -> str:
    """A team's quota row is visible to its members (and system/admin).

    Writes stay system/admin only (quotas are set by platform admins); the
    membership probe reads ``team_members`` (non-recursive), so this is
    cycle-free like ``teams_select_predicate``.
    """
    return (
        f"{_system_or_admin()} "
        "OR EXISTS (SELECT 1 FROM team_members "
        f"WHERE team_members.team_id = team_quotas.team_id AND team_members.user_id = {_USER_ID})"
    )


def team_members_select_predicate() -> str:
    """A membership row is visible to its own subject (and system/admin).

    The predicate intentionally does not probe ``team_members`` for co-members:
    a self-referential RLS predicate recurses under ``NOBYPASSRLS`` roles.
    Listing every member of a team runs under a system scope in the service.
    """
    return f"{_system_or_admin()} OR user_id = {_USER_ID}"


def invitations_predicate() -> str:
    """Team invitations are visible/writable to members of the target team.

    Platform invitations carry a NULL ``team_id`` and remain system/admin only;
    the acceptance/registration flows that must read an invitation before the
    caller is a member run under a system authorization scope.
    """
    return (
        f"{_system_or_admin()} "
        "OR (team_id IS NOT NULL AND EXISTS (SELECT 1 FROM team_members "
        f"WHERE team_members.team_id = invitations.team_id AND team_members.user_id = {_USER_ID}))"
    )


def audit_logs_select_predicate() -> str:
    """Only system/admin may enumerate the audit trail (auditor read-all is
    layered on centrally by ``policy_statements``)."""
    return _system_or_admin()


def audit_logs_write_predicate() -> str:
    """Only system/admin scopes may append to the audit chain; all audit writes
    are funneled through a system-scoped recorder (``AuditService.record`` and
    the auth flows). UPDATE/DELETE stay blocked by the database immutability
    trigger regardless of this predicate."""
    return _system_or_admin()


def apply_row_level_security(execute) -> None:
    """Recreate the full tenant RLS policy matrix.

    Callers pass an ``execute(statements)`` callback (a list[str] runner). This
    is the single canonical matrix for every tenant-owned table; the auditor
    read-all / no-write guarantee is baked into ``policy_statements`` itself.
    """
    for table in sorted(PRIVATE_ROOT_TABLES):
        execute(policy_statements(table))
    for table in sorted(VISIBILITY_ROOT_TABLES):
        execute(policy_statements(table, has_visibility=True))
    for table in sorted(POLICY_ROOT_TABLES):
        execute(
            policy_statements(
                table,
                select_predicate=policy_control_plane_predicate(),
                write_predicate=policy_control_plane_predicate(),
            )
        )
    for table, (parent, foreign_key, parent_key) in sorted(CHILD_TABLES.items()):
        execute(
            policy_statements(
                table,
                inherited_predicate=child_predicate(table, parent, foreign_key, parent_key),
            )
        )
    for table in sorted(EXECUTION_ROOT_TABLES):
        execute(policy_statements(table))
    execute(
        policy_statements(
            "inference_bindings",
            select_predicate=preference_select_predicate(),
            write_predicate=preference_write_predicate(),
        )
    )
    for table, column in _CUSTOM_OWNER_TABLES.items():
        predicate = _custom_owner_predicate(column)
        execute(
            policy_statements(
                table,
                select_predicate=predicate,
                write_predicate=predicate,
            )
        )
    # Identity / tenancy tables. Reads are scoped to the principal (self, peers,
    # team members); every write predicate is system/admin only, because the
    # legitimate multi-user mutations (login, registration, invitation
    # acceptance, member management) run under a system authorization scope where
    # the service layer is the authorization boundary.
    execute(
        policy_statements(
            "users",
            select_predicate=users_select_predicate(),
            write_predicate=_system_or_admin(),
        )
    )
    execute(
        policy_statements(
            "teams",
            select_predicate=teams_select_predicate(),
            write_predicate=_system_or_admin(),
        )
    )
    execute(
        policy_statements(
            "team_members",
            select_predicate=team_members_select_predicate(),
            write_predicate=_system_or_admin(),
        )
    )
    execute(
        policy_statements(
            "invitations",
            select_predicate=invitations_predicate(),
            write_predicate=invitations_predicate(),
        )
    )
    execute(
        policy_statements(
            "audit_logs",
            select_predicate=audit_logs_select_predicate(),
            write_predicate=audit_logs_write_predicate(),
        )
    )
    execute(
        policy_statements(
            "team_quotas",
            select_predicate=team_quotas_select_predicate(),
            write_predicate=_system_or_admin(),
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
