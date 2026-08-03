"""enable tenant row level security

Revision ID: ii04tenantrls
Revises: hh03tenantteams
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op

from app.infrastructure.security.tenant_rls import (
    child_predicate,
    config_select_predicate,
    config_write_predicate,
    disable_policy_statements,
    policy_statements,
)

revision: str = "ii04tenantrls"
down_revision: Union[str, None] = "hh03tenantteams"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Freeze the table catalog at this revision. Runtime policy catalogs grow as
# later migrations add tables; historical migrations must never consume that
# mutable catalog or a fresh install would reference tables that do not exist
# yet. Policy SQL helpers remain shared so predicate semantics stay consistent.
_PRIVATE_ROOT_TABLES = {
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "codebases",
    "files",
    "llm_token_usages",
    "scheduled_jobs",
}
_VISIBILITY_ROOT_TABLES = {
    "llm_models",
    "llm_endpoints",
    "skills",
    "mcp_servers",
    "a2a_servers",
}
_CONFIG_ROOT_TABLES = {
    "app_configs",
    "app_config_revisions",
}
_CHILD_TABLES = {
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


def upgrade() -> None:
    for table_name in sorted(_PRIVATE_ROOT_TABLES):
        for statement in policy_statements(table_name):
            op.execute(statement)
    for table_name in sorted(_VISIBILITY_ROOT_TABLES):
        for statement in policy_statements(table_name, has_visibility=True):
            op.execute(statement)
    for table_name in sorted(_CONFIG_ROOT_TABLES):
        for statement in policy_statements(
            table_name,
            select_predicate=config_select_predicate(),
            write_predicate=config_write_predicate(),
        ):
            op.execute(statement)
    for table_name, (parent, foreign_key, parent_key) in sorted(_CHILD_TABLES.items()):
        predicate = child_predicate(table_name, parent, foreign_key, parent_key)
        for statement in policy_statements(
            table_name,
            inherited_predicate=predicate,
        ):
            op.execute(statement)


def downgrade() -> None:
    tables = (
        set(_CHILD_TABLES)
        | _CONFIG_ROOT_TABLES
        | _PRIVATE_ROOT_TABLES
        | _VISIBILITY_ROOT_TABLES
    )
    for table_name in sorted(tables, reverse=True):
        for statement in disable_policy_statements(table_name):
            op.execute(statement)
