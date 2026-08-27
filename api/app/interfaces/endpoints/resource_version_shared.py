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

from typing import Annotated, Any, TypeVar

from fastapi import Depends
from pydantic import BaseModel

from app.domain.models.knowledge_version import mutable_json_value
from app.domain.models.scope import Principal, WorkspaceContext
from app.interfaces.auth_dependencies import get_workspace_context, require_non_auditor

TResponse = TypeVar("TResponse", bound=BaseModel)


def to_version_response[TResponse: BaseModel](
    response_cls: type[TResponse], version: Any
) -> TResponse:
    """Map a domain version object to its resource-specific response schema."""
    if isinstance(version, BaseModel):
        return response_cls.model_validate(mutable_json_value(version.model_dump(mode="python")))
    return response_cls.model_validate(version, from_attributes=True)


def to_build_response[TResponse: BaseModel](response_cls: type[TResponse], build: Any) -> TResponse:
    """Map a domain build object to its resource-specific response schema."""
    if isinstance(build, BaseModel):
        return response_cls.model_validate(mutable_json_value(build.model_dump(mode="python")))
    return response_cls.model_validate(build, from_attributes=True)


# Keep workspace authorization and the auditor write guard as independently
# inspectable route dependencies so RBAC composition remains fail-closed.
WorkspaceContextDep = Annotated[WorkspaceContext, Depends(get_workspace_context)]
NonAuditorWriteGuardDep = Annotated[Principal, Depends(require_non_auditor)]
