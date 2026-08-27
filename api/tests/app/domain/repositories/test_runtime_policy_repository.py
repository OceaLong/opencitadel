from importlib import import_module
from inspect import signature


def test_runtime_policy_repository_exposes_atomic_head_operations() -> None:
    repository_module = import_module("app.domain.repositories.runtime_policy_repository")
    repository = repository_module.RuntimePolicyRepository

    assert {
        "seed_if_missing",
        "load_head",
        "load_active_pair",
        "load_execution_revision",
        "load_operations_revision",
        "list_execution_revisions",
        "list_operations_revisions",
        "create_and_activate_execution",
        "create_and_activate_operations",
    } <= set(repository.__dict__)
    create_execution = signature(repository.create_and_activate_execution)
    assert "expected_head_version" in create_execution.parameters
    assert "expected_active_revision_id" in create_execution.parameters
    assert "restored_from_id" in create_execution.parameters
