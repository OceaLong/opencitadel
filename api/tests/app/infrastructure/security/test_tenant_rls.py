#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.security.tenant_rls import (
    CHILD_TABLES,
    CONFIG_ROOT_TABLES,
    PRIVATE_ROOT_TABLES,
    VISIBILITY_ROOT_TABLES,
    config_select_predicate,
    config_write_predicate,
    preference_select_predicate,
    preference_write_predicate,
    direct_select_predicate,
    direct_write_predicate,
    policy_statements,
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


def test_config_policy_allows_owner_override_but_protects_global_writes():
    select_predicate = config_select_predicate()
    write_predicate = config_write_predicate()

    assert "scope = 'global'" in select_predicate
    assert "scope = 'user'" in select_predicate
    assert "owner_user_id" in select_predicate
    assert "scope = 'global'" not in write_predicate
    assert "app.is_admin" in write_predicate


def test_model_preference_policy_separates_global_user_and_team_bindings():
    select_predicate = preference_select_predicate()
    write_predicate = preference_write_predicate()

    assert "scope_type = 'global'" in select_predicate
    assert "scope_type = 'user'" in select_predicate
    assert "scope_type = 'team'" in select_predicate
    assert "scope_type = 'global'" not in write_predicate
    assert "app.is_admin" in write_predicate


def test_rls_policy_matrix_covers_root_and_child_tenant_tables():
    root_tables = PRIVATE_ROOT_TABLES | VISIBILITY_ROOT_TABLES | CONFIG_ROOT_TABLES

    assert {
        "sessions",
        "memory_entries",
        "knowledge_bases",
        "codebases",
        "files",
        "llm_token_usages",
        "llm_models",
        "llm_endpoints",
        "skills",
        "mcp_servers",
        "a2a_servers",
        "scheduled_jobs",
        "app_configs",
        "app_config_revisions",
    } <= root_tables
    assert {
        "session_events",
        "session_checkpoints",
        "session_agent_memories",
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

    statements = policy_statements("llm_models", has_visibility=True)
    assert any("FORCE ROW LEVEL SECURITY" in statement for statement in statements)
    assert any("FOR SELECT" in statement for statement in statements)
    assert any("FOR INSERT" in statement for statement in statements)
    assert any("FOR UPDATE" in statement for statement in statements)
    assert any("FOR DELETE" in statement for statement in statements)
