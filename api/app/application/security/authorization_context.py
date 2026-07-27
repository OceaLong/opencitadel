#!/usr/bin/env python
# -*- coding: utf-8 -*-
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

from app.domain.models.authorization import AuthorizationContext


_current_authorization: ContextVar[AuthorizationContext] = ContextVar(
    "current_authorization",
    default=AuthorizationContext.anonymous(),
)


def get_authorization_context() -> AuthorizationContext:
    return _current_authorization.get()


def set_authorization_context(context: AuthorizationContext) -> Token:
    return _current_authorization.set(context)


def reset_authorization_context(token: Token) -> None:
    _current_authorization.reset(token)


@contextmanager
def authorization_scope(context: AuthorizationContext) -> Iterator[AuthorizationContext]:
    token = set_authorization_context(context)
    try:
        yield context
    finally:
        reset_authorization_context(token)
