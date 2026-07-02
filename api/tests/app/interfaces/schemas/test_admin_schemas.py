#!/usr/bin/env python
# -*- coding: utf-8
from datetime import datetime, timedelta

import pytest

from app.interfaces.schemas.admin import InvitationStatus, PlatformInvitationResponse
from app.domain.models.invitation import Invitation, InvitationType


def test_platform_invitation_status_pending():
    invitation = Invitation(
        email="user@example.com",
        token="token",
        expires_at=datetime.now() + timedelta(days=1),
    )
    response = PlatformInvitationResponse.from_domain(invitation, now=datetime.now())
    assert response.status == InvitationStatus.PENDING


def test_platform_invitation_status_accepted():
    now = datetime.now()
    invitation = Invitation(
        email="user@example.com",
        token="token",
        expires_at=now + timedelta(days=1),
        accepted_at=now,
    )
    response = PlatformInvitationResponse.from_domain(invitation, now=now)
    assert response.status == InvitationStatus.ACCEPTED


def test_platform_invitation_status_expired():
    now = datetime.now()
    invitation = Invitation(
        email="user@example.com",
        token="token",
        expires_at=now - timedelta(days=1),
    )
    response = PlatformInvitationResponse.from_domain(invitation, now=now)
    assert response.status == InvitationStatus.EXPIRED


@pytest.mark.parametrize(
    ("dimension",),
    [
        ("model",),
        ("user",),
        ("team",),
        ("agent",),
    ],
)
def test_usage_breakdown_dimension_literal(dimension: str) -> None:
    from app.application.services.usage_stats_service import UsageBreakdownDimension

    assert dimension in ("model", "user", "team", "agent")
