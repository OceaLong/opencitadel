"""Response mappers and write-guard dependency for resource routers."""

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
