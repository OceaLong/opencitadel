from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CI_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/ci.yml"
E2E_ENVIRONMENT = REPOSITORY_ROOT / ".env.e2e"
CANONICAL_POSTGRES_TESTS = (
    REPOSITORY_ROOT / "api/tests/app/infrastructure/repositories/test_db_memory_vector_search.py",
    REPOSITORY_ROOT
    / "api/tests/app/infrastructure/repositories/test_db_versioned_retrieval_postgres.py",
    REPOSITORY_ROOT
    / "api/tests/app/infrastructure/repositories/test_db_versioned_graph_postgres.py",
    REPOSITORY_ROOT
    / "api/tests/app/infrastructure/repositories/test_db_resource_binding_postgres.py",
    REPOSITORY_ROOT / "api/tests/app/infrastructure/security/test_tenant_rls_integration.py",
)


def test_canonical_postgres_gates_have_no_opt_in_skip() -> None:
    sources = {path: path.read_text() for path in (*CANONICAL_POSTGRES_TESTS, CI_WORKFLOW)}

    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path, text in sources.items()
        if "OPENCITADEL_RUN_POSTGRES_INTEGRATION" in text
    ]

    assert offenders == []


def test_canonical_postgres_tests_use_shared_availability_fixture() -> None:
    missing = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in CANONICAL_POSTGRES_TESTS
        if 'usefixtures("postgres_integration")' not in path.read_text()
    ]

    assert missing == []


def test_ci_requires_postgres_tests_in_default_api_job() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert 'OPENCITADEL_REQUIRE_POSTGRES_TESTS: "1"' in workflow


def test_e2e_environment_provisions_all_database_roles() -> None:
    environment = E2E_ENVIRONMENT.read_text()

    assert "BOOTSTRAP_ADMIN_PASSWORD=CHANGE-ME-E2E" not in environment
    for name in (
        "POSTGRES_MIGRATION_USER",
        "POSTGRES_MIGRATION_PASSWORD",
        "POSTGRES_KERNEL_USER",
        "POSTGRES_KERNEL_PASSWORD",
    ):
        assert f"{name}=" in environment
