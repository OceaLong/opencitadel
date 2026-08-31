"""The notification type enum must cover every type the services actually send."""

import pytest

from app.domain.models.notification import Notification


@pytest.mark.parametrize(
    "notification_type",
    [
        "job_started",
        "job_complete",
        "job_failed",
        "patrol_complete",
        "artifact_final",
        "approval_waiting",
    ],
)
def test_notification_accepts_types_emitted_by_services(notification_type: str) -> None:
    # Regression: scheduled_job_service.trigger_job sends "job_started" AFTER the
    # session is already committed. A missing enum member raised a pydantic
    # ValidationError (a ValueError subclass) that the scheduler loop swallowed
    # and mislabeled the run FAILED -- starving job_complete and re-spawning an
    # orphan session every tick. Every type any service emits must validate.
    notification = Notification(user_id="u1", type=notification_type, message="x")
    assert notification.type == notification_type
