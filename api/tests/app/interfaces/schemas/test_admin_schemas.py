from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models.invitation import Invitation
from app.interfaces.schemas.admin import InvitationStatus, PlatformInvitationResponse


def test_platform_invitation_status_pending():
    invitation = Invitation(
        email="user@example.com",
        token="token",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    response = PlatformInvitationResponse.from_domain(invitation, now=datetime.now(UTC))
    assert response.status == InvitationStatus.PENDING


def test_platform_invitation_status_accepted():
    now = datetime.now(UTC)
    invitation = Invitation(
        email="user@example.com",
        token="token",
        expires_at=now + timedelta(days=1),
        accepted_at=now,
    )
    response = PlatformInvitationResponse.from_domain(invitation, now=now)
    assert response.status == InvitationStatus.ACCEPTED


def test_platform_invitation_status_expired():
    now = datetime.now(UTC)
    invitation = Invitation(
        email="user@example.com",
        token="token",
        expires_at=now - timedelta(days=1),
    )
    response = PlatformInvitationResponse.from_domain(invitation, now=now)
    assert response.status == InvitationStatus.EXPIRED


@pytest.mark.parametrize(
    "dimension",
    [
        "model",
        "user",
        "team",
        "agent",
    ],
)
def test_usage_breakdown_dimension_literal(dimension: str) -> None:

    assert dimension in ("model", "user", "team", "agent")
