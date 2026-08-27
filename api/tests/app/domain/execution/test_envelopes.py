from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.execution.activity import ActivityRequest
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.events import NewEvent
from app.domain.execution.timer import ScheduledCommandRequest

UTC_NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


def make_command(**overrides: object) -> CommandEnvelope:
    values: dict[str, object] = {
        "command_id": uuid4(),
        "command_type": "RequestSyntheticRun",
        "command_schema_version": 1,
        "stream_type": "synthetic_run",
        "stream_id": "run-1",
        "expected_stream_version": 0,
        "owner_user_id": "user-1",
        "team_id": None,
        "correlation_id": uuid4(),
        "causation_id": None,
        "issued_at": UTC_NOW,
        "payload": {"nested": [1, True, None, {"label": "safe"}]},
    }
    values.update(overrides)
    return CommandEnvelope.model_validate(values)


@pytest.mark.parametrize(
    ("owner_user_id", "team_id"),
    [(None, None), ("user-1", "team-1")],
)
def test_command_requires_exactly_one_owner_scope(
    owner_user_id: str | None,
    team_id: str | None,
) -> None:
    with pytest.raises(ValidationError):
        make_command(owner_user_id=owner_user_id, team_id=team_id)


def test_command_is_frozen_and_normalizes_aware_timestamp_to_utc() -> None:
    issued_at = datetime(2026, 8, 20, 18, 0, tzinfo=timezone(timedelta(hours=8)))

    command = make_command(issued_at=issued_at)

    assert command.issued_at == UTC_NOW
    assert command.issued_at.tzinfo is UTC
    with pytest.raises(ValidationError):
        command.stream_id = "other"  # type: ignore[misc]


def test_command_payload_is_defensively_copied_and_deeply_immutable() -> None:
    source = {"nested": [{"label": "before"}]}

    command = make_command(payload=source)
    source["nested"][0]["label"] = "outside"  # type: ignore[index]

    assert command.payload == {"nested": [{"label": "before"}]}
    with pytest.raises(TypeError, match="immutable"):
        command.payload["nested"][0]["label"] = "inside"  # type: ignore[index]
    with pytest.raises(TypeError, match="immutable"):
        command.payload["nested"].append("inside")  # type: ignore[union-attr]


@pytest.mark.parametrize("field", ["issued_at"])
def test_command_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValidationError):
        make_command(**{field: UTC_NOW.replace(tzinfo=None)})


def test_command_rejects_negative_expected_version() -> None:
    with pytest.raises(ValidationError):
        make_command(expected_stream_version=-1)


@pytest.mark.parametrize(
    "payload",
    [
        {"at": UTC_NOW},
        {"raw": b"secret"},
        {"items": {"not", "json"}},
        {"bad_key": {1: "value"}},
        {"not_finite": float("nan")},
    ],
)
def test_command_payload_is_strict_json(payload: object) -> None:
    with pytest.raises(ValidationError):
        make_command(payload=payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"password": "do-not-store"},
        {"nested": {"authorization": "Bearer value"}},
        {"headers": [{"cookie": "session=value"}]},
        {"apiToken": "do-not-store"},
        {"client_secret": "do-not-store"},
    ],
)
def test_public_event_payload_rejects_secret_fields(payload: object) -> None:
    with pytest.raises(ValidationError):
        NewEvent(
            event_type="SyntheticRunRequested",
            event_schema_version=1,
            public_payload=payload,
            internal_payload={},
        )


def test_internal_event_payload_may_hold_sanitized_diagnostics() -> None:
    event = NewEvent(
        event_type="SyntheticRunFailed",
        event_schema_version=1,
        public_payload={"failure_code": "MODEL_UNAVAILABLE"},
        internal_payload={"provider_category": "capacity"},
        secret_ref="objects/execution/run-1/failure-1",
    )

    assert event.public_payload == {"failure_code": "MODEL_UNAVAILABLE"}
    assert event.secret_ref == "objects/execution/run-1/failure-1"


def test_event_payloads_are_defensively_copied_and_deeply_immutable() -> None:
    public_source = {"nested": [{"label": "before"}]}
    internal_source = {"details": ["before"]}

    event = NewEvent(
        event_type="SyntheticRunFailed",
        event_schema_version=1,
        public_payload=public_source,
        internal_payload=internal_source,
    )
    public_source["nested"][0]["label"] = "outside"  # type: ignore[index]
    internal_source["details"].append("outside")  # type: ignore[union-attr]

    assert event.public_payload == {"nested": [{"label": "before"}]}
    assert event.internal_payload == {"details": ["before"]}
    with pytest.raises(TypeError, match="immutable"):
        event.public_payload["nested"][0]["label"] = "inside"  # type: ignore[index]
    with pytest.raises(TypeError, match="immutable"):
        event.internal_payload["details"].append("inside")  # type: ignore[union-attr]


def test_activity_and_timer_timestamps_are_utc_aware() -> None:
    activity = ActivityRequest(
        activity_id=uuid4(),
        activity_type="SyntheticActivity",
        aggregate_type="synthetic_run",
        aggregate_id="run-1",
        generation=0,
        timeout_at=UTC_NOW,
        input_ref=None,
        input_digest="sha256:abc",
    )
    timer = ScheduledCommandRequest(
        timer_id=uuid4(),
        due_at=UTC_NOW,
        command=make_command(),
        cancellation_event_types=frozenset({"SyntheticRunCompleted"}),
    )

    assert activity.timeout_at.tzinfo is UTC
    assert timer.due_at.tzinfo is UTC

    with pytest.raises(ValidationError):
        activity.timeout_at = UTC_NOW  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ScheduledCommandRequest(
            timer_id=uuid4(),
            due_at=UTC_NOW.replace(tzinfo=None),
            command=make_command(),
            cancellation_event_types=frozenset(),
        )
