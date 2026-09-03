"""The DB authorization trust domain keys off database_authorization_signing_secret.

The initial migration stamps the database-side HMAC secret into
``execution_authorization_secrets``, and every session factory / unit of work
signs the per-transaction authorization context against it. Historically some
call sites signed with ``settings.session_secret`` (the Starlette cookie
secret) while others used ``settings.database_authorization_signing_secret``;
because the latter only *defaults* to the former, any explicit configuration
split the two trust domains and every RLS-protected write failed signature
validation. These contracts pin all DB-authorization call sites to the single
dedicated setting.
"""

from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[3]

_DB_AUTHORIZATION_CALL_SITES = (
    "alembic/env.py",
    "app/migrate_runtime_policy_seed.py",
    "app/rotate_db_signing_secret.py",
    "app/infrastructure/storage/postgres.py",
    "app/seed_demo.py",
)


@pytest.mark.parametrize("relative_path", _DB_AUTHORIZATION_CALL_SITES)
def test_db_authorization_call_sites_use_dedicated_secret(relative_path: str) -> None:
    source = (_API_ROOT / relative_path).read_text(encoding="utf-8")
    assert "database_authorization_signing_secret" in source, (
        f"{relative_path} must sign the DB authorization context with "
        "settings.database_authorization_signing_secret"
    )
    stripped = source.replace("database_authorization_signing_secret", "")
    assert "session_secret" not in stripped, (
        f"{relative_path} must not reference session_secret: the cookie trust "
        "domain and the DB authorization trust domain are separate, and mixing "
        "them breaks RLS signature validation whenever "
        "DATABASE_AUTHORIZATION_SIGNING_SECRET is configured explicitly"
    )
