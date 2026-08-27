"""Execution-kernel probes distinguish process health from durable readiness."""

import json

import pytest

from app.execution_kernel_health import (
    HealthCheckError,
    check_database_readiness,
    check_liveness,
    check_runtime_policy_readiness,
)


def test_liveness_requires_a_live_pid_and_fresh_kernel_heartbeat(tmp_path):
    marker = tmp_path / "execution-kernel.health"
    marker.write_text(
        json.dumps({"pid": 42, "updated_at_epoch": 90.0}),
        encoding="utf-8",
    )

    check_liveness(
        marker,
        now_epoch=100.0,
        max_age_seconds=30.0,
        process_probe=lambda pid: pid == 42,
    )

    with pytest.raises(HealthCheckError, match="stale"):
        check_liveness(
            marker,
            now_epoch=121.0,
            max_age_seconds=30.0,
            process_probe=lambda _pid: True,
        )
    with pytest.raises(HealthCheckError, match="process"):
        check_liveness(
            marker,
            now_epoch=100.0,
            max_age_seconds=30.0,
            process_probe=lambda _pid: False,
        )


def test_readiness_rejects_degraded_runtime_policy_marker(tmp_path):
    marker = tmp_path / "execution-kernel.health"
    marker.write_text(
        json.dumps(
            {
                "pid": 42,
                "updated_at_epoch": 90.0,
                "runtime_policy_ready": False,
                "runtime_policy_error_key": "runtimePolicy.integrity",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(HealthCheckError, match=r"runtimePolicy\.integrity"):
        check_runtime_policy_readiness(marker)


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, statement):
        self.statement = statement

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_instance = _Cursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def cursor(self):
        return self.cursor_instance


def test_readiness_requires_kernel_role_schema_and_append_privileges():
    connection = _Connection(
        (
            "opencitadel_execution_kernel_runtime",
            True,
            True,
            True,
            False,
        )
    )

    check_database_readiness(
        dsn="postgresql://kernel:test@postgres/opencitadel",
        expected_user="opencitadel_execution_kernel_runtime",
        connect=lambda *_args, **_kwargs: connection,
    )

    assert "execution_command_inbox" in connection.cursor_instance.statement
    assert "execution_events" in connection.cursor_instance.statement


def test_readiness_rejects_the_api_database_role():
    connection = _Connection(("opencitadel_app", True, True, True, False))

    with pytest.raises(HealthCheckError, match="database role"):
        check_database_readiness(
            dsn="postgresql://api:test@postgres/opencitadel",
            expected_user="opencitadel_execution_kernel_runtime",
            connect=lambda *_args, **_kwargs: connection,
        )
