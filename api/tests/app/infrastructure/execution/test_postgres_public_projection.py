"""Opaque cursor and formal public-event query contracts."""

import inspect

import pytest

from app.application.execution.public_projection import PublicEventCursor
from app.infrastructure.execution.postgres_public_projection import (
    PostgresPublicProjection,
)


def test_cursor_is_opaque_integrity_protected_and_round_trips_position() -> None:
    codec = PublicEventCursor(secret=b"cursor-test-secret")

    encoded = codec.encode(1234)

    assert encoded != "1234"
    assert codec.decode(encoded) == 1234
    with pytest.raises(ValueError, match="cursor"):
        codec.decode(encoded[:-1] + ("A" if encoded[-1] != "A" else "B"))


def test_repository_queries_only_the_formal_public_projection() -> None:
    source = inspect.getsource(PostgresPublicProjection)

    assert "ExecutionPublicEventORM" in source
    assert "source_entity_type" in source
    assert "source_entity_id" in source
    assert "session_events" not in source
    assert "SessionEventModel" not in source
