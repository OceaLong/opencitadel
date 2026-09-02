from __future__ import annotations

import inspect

from app.execution_kernel_health import check_database_readiness


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str) -> None:
        assert "'DELETE'" not in statement

    def fetchone(self):
        return ("kernel_role", True, True, True, False)


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> Cursor:
        return Cursor()


def test_readiness_allows_trigger_gated_purge_delete_privilege() -> None:
    assert "'DELETE'" not in inspect.getsource(check_database_readiness)

    check_database_readiness(
        dsn="postgresql://unused",
        expected_user="kernel_role",
        connect=lambda *_args, **_kwargs: Connection(),
    )
