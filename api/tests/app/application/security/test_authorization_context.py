#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.security.authorization_context import (
    authorization_scope,
    get_authorization_context,
)
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole


def test_user_context_captures_principal_workspace_roles_and_request():
    principal = Principal(
        user_id="user-1",
        global_role=GlobalRole.USER,
        team_roles={"team-1": TeamRole.ADMIN},
    )

    context = AuthorizationContext.for_principal(
        principal,
        scope=OwnerScope.team("user-1", "team-1"),
        request_id="request-1",
    )

    assert context.mode == AuthorizationMode.USER
    assert context.user_id == "user-1"
    assert context.team_id == "team-1"
    assert context.team_role == TeamRole.ADMIN
    assert context.request_id == "request-1"
    assert context.is_admin is False


def test_authorization_scope_resets_system_capability():
    assert get_authorization_context().mode == AuthorizationMode.ANONYMOUS

    with authorization_scope(AuthorizationContext.system("worker")):
        assert get_authorization_context().mode == AuthorizationMode.SYSTEM
        assert get_authorization_context().system_actor == "worker"

    assert get_authorization_context().mode == AuthorizationMode.ANONYMOUS
