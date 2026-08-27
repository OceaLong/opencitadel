from pathlib import Path

ROOT = Path(__file__).parents[5]


def test_postgres_init_keeps_ddl_ownership_away_from_application_role() -> None:
    source = (ROOT / "deploy/helm/opencitadel/files/postgres/init-app-role.sh").read_text()

    assert "opencitadel_execution_api" in source
    assert "opencitadel_execution_kernel" in source
    assert "OPENCITADEL_MIGRATION_USER" in source
    assert "OPENCITADEL_APP_USER" in source
    assert "OPENCITADEL_KERNEL_USER" in source
    assert "GRANT opencitadel_execution_api" in source
    assert "GRANT opencitadel_execution_kernel" in source
    assert "NOINHERIT" in source
    assert "GRANT ALL ON SCHEMA public" not in source
    assert "ALTER SCHEMA public OWNER TO" not in source
    assert "ALTER DATABASE %I OWNER TO %I" not in source
    assert "OWNER TO %I" not in source


def test_alembic_uses_admin_connection_and_passes_runtime_roles() -> None:
    source = (ROOT / "api/alembic/env.py").read_text()

    assert "sqlalchemy_sync_migration_database_uri" in source
    assert "app.runtime_database_role" in source
    assert "app.execution_runtime_role" in source


def test_runtime_group_roles_receive_schema_usage() -> None:
    source = (ROOT / "api/alembic/versions/0001greenfield_initial.py").read_text()

    assert "GRANT USAGE ON SCHEMA public TO %I, %I" in source


def test_api_runtime_role_can_read_the_migration_head_only() -> None:
    source = (ROOT / "api/alembic/versions/0001greenfield_initial.py").read_text()

    assert "GRANT SELECT ON alembic_version TO %I" in source
    assert "GRANT ALL ON alembic_version" not in source


def test_migration_role_can_delegate_only_schema_usage() -> None:
    sources = (
        ROOT / "deploy/helm/opencitadel/files/postgres/init-app-role.sh",
        ROOT / "api/scripts/prepare_ci_database.py",
    )

    for path in sources:
        source = path.read_text()
        assert "WITH GRANT OPTION" in source
        assert "GRANT ALL ON SCHEMA" not in source


def test_database_bootstrap_installs_every_extension_before_non_superuser_migration() -> None:
    sources = (
        ROOT / "deploy/helm/opencitadel/files/postgres/init-app-role.sh",
        ROOT / "api/scripts/prepare_ci_database.py",
    )

    for path in sources:
        source = path.read_text()
        assert "vector" in source
        assert "uuid-ossp" in source
        assert "pgcrypto" in source


def test_runtime_containers_mask_migration_credentials() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    api_section = compose.split("  opencitadel-api:", maxsplit=1)[1].split(
        "  opencitadel-execution-kernel:", maxsplit=1
    )[0]
    kernel_section = compose.split("  opencitadel-execution-kernel:", maxsplit=1)[1].split(
        "  opencitadel-ui:", maxsplit=1
    )[0]

    for section in (api_section, kernel_section):
        assert 'POSTGRES_ADMIN_USER: ""' in section
        assert 'POSTGRES_ADMIN_PASSWORD: ""' in section
        assert 'POSTGRES_MIGRATION_USER: ""' in section
        assert 'POSTGRES_MIGRATION_PASSWORD: ""' in section

    assert 'POSTGRES_KERNEL_USER: ""' in api_section
    assert 'POSTGRES_KERNEL_PASSWORD: ""' in api_section

    assert "POSTGRES_KERNEL_USER" in kernel_section
    assert "app.execution_kernel_main" in kernel_section
