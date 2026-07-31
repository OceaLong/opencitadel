#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Shared response mappers and write-guard dependency for resource routers.

`codebase_routes.py` and `knowledge_base_routes.py` each publish a version/
build lifecycle for their own resource kind. Their per-resource `*Response`
schemas are not field-identical (e.g. `CodebaseVersionResponse` carries
`codebase_id`/`source_*` fields that `KnowledgeVersionResponse` does not have,
and the id field is named `codebase_id` vs. `knowledge_base_id`), so the
schemas themselves stay separate and un-merged.

What *is* identical between the two files is the mapper body: both
`_to_version_response`/`_to_build_response` implementations were already
fully generic one-liners (`ResponseCls.model_validate(obj, from_attributes=True)`)
with no field-level logic of their own — the field extraction is delegated
entirely to pydantic based on the target response class. That means the two
implementations can be merged without any "differing field" switch beyond the
target response class itself, which becomes an explicit parameter here.
"""
from typing import Annotated, Any, Type, TypeVar

from fastapi import Depends
from pydantic import BaseModel

from app.domain.models.scope import Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor

TResponse = TypeVar("TResponse", bound=BaseModel)


def to_version_response(response_cls: Type[TResponse], version: Any) -> TResponse:
    """Map a domain version object to its resource-specific response schema."""
    return response_cls.model_validate(version, from_attributes=True)


def to_build_response(response_cls: Type[TResponse], build: Any) -> TResponse:
    """Map a domain build object to its resource-specific response schema."""
    return response_cls.model_validate(build, from_attributes=True)


# The two dependencies below are always used together on mutation routes, in
# this exact order: `WorkspaceContextDep` resolves and authorizes the
# workspace scope first, then `NonAuditorWriteGuardDep` rejects the read-only
# auditor role. Both files previously spelled out
# `ctx: WorkspaceContext = Depends(get_workspace_context)` followed by
# `_write_guard = Depends(require_non_auditor)` on every write route; sharing
# these two named aliases keeps that pairing defined in one place.
#
# Deliberately kept as two parameters (not collapsed into a single combined
# dependency): existing route-level unit tests invoke these router functions
# directly with positional arguments shaped as
# `(..., ctx, write_guard_principal, service, ...)`, and a separate RBAC
# regression test asserts `require_non_auditor` is present as a *top-level*
# dependency of each mutation route's `Dependant` (nested sub-dependencies of
# a combined dependency would not show up there). Using two Annotated aliases
# preserves parameter count, order, and top-level dependency identity exactly
# as before, so no test needed to change.
WorkspaceContextDep = Annotated[WorkspaceContext, Depends(get_workspace_context)]
NonAuditorWriteGuardDep = Annotated[Principal, Depends(require_non_auditor)]
