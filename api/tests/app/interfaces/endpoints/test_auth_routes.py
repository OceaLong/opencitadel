"""OAuth registration invitation resolution must reject expired invitations."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.interfaces.endpoints.auth_routes import _resolve_oauth_registration_invitation


def _uow_with_platform_invitations(invitations):
    return SimpleNamespace(
        invitation=SimpleNamespace(
            get_by_token=AsyncMock(return_value=None),
            list=AsyncMock(return_value=invitations),
        )
    )


@pytest.mark.asyncio
async def test_oauth_platform_invitation_rejects_expired():
    # Regression: the OAuth PLATFORM branch only checked `not accepted`, so an
    # expired invitation could still be redeemed via SSO (the password path and
    # the TEAM branch both check expires_at).
    expired = SimpleNamespace(
        email="user@example.com",
        accepted=False,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    uow = _uow_with_platform_invitations([expired])

    result = await _resolve_oauth_registration_invitation(
        uow, email="user@example.com", team_invite_token=""
    )

    assert result is None


@pytest.mark.asyncio
async def test_oauth_platform_invitation_accepts_valid():
    valid = SimpleNamespace(
        email="user@example.com",
        accepted=False,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    uow = _uow_with_platform_invitations([valid])

    result = await _resolve_oauth_registration_invitation(
        uow, email="user@example.com", team_invite_token=""
    )

    assert result is valid
