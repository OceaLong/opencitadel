from types import SimpleNamespace

import pytest

from app.application.security.authorization_context import (
    get_authorization_context,
    set_authorization_context,
)
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
from app.domain.models.scope import Principal
from app.domain.models.team import TeamRole
from app.interfaces.auth_context import set_principal
from app.interfaces.auth_dependencies import get_workspace_context


@pytest.mark.asyncio
async def test_workspace_dependency_binds_team_authorization_context():
    principal_token = set_principal(
        Principal(
            user_id="user-1",
            team_roles={"team-1": TeamRole.MEMBER},
        )
    )
    authorization_token = set_authorization_context(AuthorizationContext.anonymous())
    try:
        request = SimpleNamespace(state=SimpleNamespace())
        context = await get_workspace_context(request, "team-1")

        authorization = get_authorization_context()
        assert context.scope.team_id == "team-1"
        assert authorization.mode == AuthorizationMode.USER
        assert authorization.user_id == "user-1"
        assert authorization.team_id == "team-1"
    finally:
        authorization_token.var.reset(authorization_token)
        principal_token.var.reset(principal_token)
