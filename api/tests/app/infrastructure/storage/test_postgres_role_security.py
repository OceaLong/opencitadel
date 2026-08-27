import pytest

from app.infrastructure.storage.postgres import ensure_rls_capable_role


@pytest.mark.parametrize(
    ("is_superuser", "bypasses_rls"),
    [(True, False), (False, True), (True, True)],
)
def test_production_rejects_database_roles_that_bypass_rls(
    is_superuser,
    bypasses_rls,
):
    with pytest.raises(RuntimeError, match="bypass row-level security"):
        ensure_rls_capable_role(
            env="production",
            role_name="unsafe",
            is_superuser=is_superuser,
            bypasses_rls=bypasses_rls,
        )


def test_production_accepts_non_superuser_non_bypass_role():
    ensure_rls_capable_role(
        env="production",
        role_name="opencitadel_app",
        is_superuser=False,
        bypasses_rls=False,
    )


def test_development_allows_local_superuser():
    ensure_rls_capable_role(
        env="development",
        role_name="postgres",
        is_superuser=True,
        bypasses_rls=True,
    )
