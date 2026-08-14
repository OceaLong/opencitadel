#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Backward-compatible re-export shim.

The exception classes formerly defined here now live in app.domain.errors
(Phase C engineering-debt cleanup — domain services need them without
reaching into the application layer). This module re-exports the full set
so existing `from app.application.errors.exceptions import ...` imports
across application/interfaces keep working unchanged.
"""
from app.domain.errors import (
    AppException,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServerRequestsError,
    TooManyRequestsError,
    UnauthorizedError,
    ValidationError,
)

__all__ = [
    "AppException",
    "BadRequestError",
    "UnauthorizedError",
    "ForbiddenError",
    "ConflictError",
    "NotFoundError",
    "ValidationError",
    "TooManyRequestsError",
    "ServerRequestsError",
]
