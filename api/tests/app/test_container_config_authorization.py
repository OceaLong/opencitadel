#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.security.authorization_context import (
    get_authorization_context,
    reset_authorization_context,
    set_authorization_context,
)
from app.container import _warm_app_config
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode


@pytest.mark.asyncio
async def test_config_warmup_uses_explicit_system_authorization():
    observed = None

    class _Provider:
        async def get(self):
            nonlocal observed
            observed = get_authorization_context()

    token = set_authorization_context(AuthorizationContext.anonymous())
    try:
        await _warm_app_config(None, _Provider())
        assert observed.mode == AuthorizationMode.SYSTEM
        assert observed.system_actor == "config-warmup"
        assert get_authorization_context().mode == AuthorizationMode.ANONYMOUS
    finally:
        reset_authorization_context(token)
