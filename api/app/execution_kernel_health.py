"""Process-local liveness and PostgreSQL-backed readiness probes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psycopg2

from core.config import (
    DeploymentSettings,
    load_deployment_settings,
    sqlalchemy_sync_database_uri,
)


class HealthCheckError(RuntimeError):
    """The execution kernel is not live or not ready."""


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def check_liveness(
    marker_path: Path,
    *,
    now_epoch: float | None = None,
    max_age_seconds: float = 30.0,
    process_probe: Callable[[int], bool] = _process_exists,
) -> None:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        pid = int(marker["pid"])
        updated_at = float(marker["updated_at_epoch"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HealthCheckError("kernel heartbeat is missing or invalid") from exc
    if not process_probe(pid):
        raise HealthCheckError("kernel process is not running")
    age = (time.time() if now_epoch is None else now_epoch) - updated_at
    if age < 0 or age > max_age_seconds:
        raise HealthCheckError(f"kernel heartbeat is stale ({age:.1f}s)")


def check_runtime_policy_readiness(marker_path: Path) -> None:
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        ready = marker["runtime_policy_ready"]
        error_key = marker.get("runtime_policy_error_key")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HealthCheckError("runtime policy readiness marker is invalid") from exc
    if ready is not True:
        raise HealthCheckError(
            f"runtime policy is not ready: {error_key or 'runtimePolicy.unavailable'}"
        )


def check_database_readiness(
    *,
    dsn: str,
    expected_user: str,
    connect=psycopg2.connect,
) -> None:
    try:
        with (
            connect(dsn, connect_timeout=5) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT
                    current_user,
                    to_regclass('public.execution_command_inbox') IS NOT NULL
                        AND to_regclass('public.execution_events') IS NOT NULL,
                    has_table_privilege(
                        current_user,
                        'public.execution_command_inbox',
                        'SELECT,INSERT'
                    ),
                    has_table_privilege(
                        current_user,
                        'public.execution_events',
                        'SELECT,INSERT'
                    ),
                    has_table_privilege(
                        current_user,
                        'public.execution_events',
                        'UPDATE'
                    ) OR has_table_privilege(
                        current_user,
                        'public.execution_events',
                        'DELETE'
                    )
                """
            )
            row = cursor.fetchone()
    except (OSError, RuntimeError, ValueError) as exc:
        raise HealthCheckError(f"PostgreSQL is unavailable: {exc}") from exc
    if row is None:
        raise HealthCheckError("PostgreSQL readiness query returned no row")
    current_user, schema_ready, inbox_append, events_append, events_mutable = row
    if current_user != expected_user:
        raise HealthCheckError(
            f"unexpected database role: expected {expected_user}, got {current_user}"
        )
    if not schema_ready:
        raise HealthCheckError("execution schema is not migrated")
    if not inbox_append or not events_append or events_mutable:
        raise HealthCheckError("execution-kernel database privileges are invalid")


def _marker_path() -> Path:
    return Path(
        os.environ.get(
            "EXECUTION_KERNEL_HEALTH_FILE",
            "/tmp/opencitadel-execution-kernel.health",
        )
    )


def main(
    argv: list[str] | None = None,
    *,
    settings: DeploymentSettings | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=("liveness", "readiness"))
    args = parser.parse_args(argv)
    try:
        check_liveness(
            _marker_path(),
            max_age_seconds=float(os.environ.get("EXECUTION_KERNEL_HEALTH_MAX_AGE_SECONDS", "30")),
        )
        if args.probe == "readiness":
            check_runtime_policy_readiness(_marker_path())
            resolved = settings or load_deployment_settings()
            check_database_readiness(
                dsn=sqlalchemy_sync_database_uri(resolved).replace(
                    "+psycopg2",
                    "",
                ),
                expected_user=resolved.postgres_user,
            )
    except (HealthCheckError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HealthCheckError",
    "check_database_readiness",
    "check_liveness",
    "check_runtime_policy_readiness",
    "main",
]
