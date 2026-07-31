#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Public, unified resource-build event projection."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
)


class ResourceBuildEventResponse(BaseModel):
    """Stable event data sent on every resource-build SSE path.

    Resource and version identity, plus degraded reasons, always come from the
    owner-scoped authoritative build row. Older builds without degradation
    metadata project an empty list.
    """

    model_config = ConfigDict(frozen=True)

    event: Literal["resource_build"] = "resource_build"
    id: str
    seq: int
    build_id: str
    resource_kind: ResourceKind
    resource_id: str
    version_id: str
    phase: str | None = None
    state: BuildState
    progress: float
    degraded_reasons: list[Any]
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_authoritative(
        cls,
        build: ResourceBuild,
        event: ResourceBuildEvent,
    ) -> "ResourceBuildEventResponse":
        if event.build_id != build.id:
            raise ValueError(
                "resource build event does not belong to authoritative build"
            )
        return cls(
            id=event.id,
            seq=event.seq,
            build_id=build.id,
            resource_kind=build.resource_kind,
            resource_id=build.resource_id,
            version_id=build.version_id,
            phase=event.phase,
            state=event.state,
            progress=event.progress,
            degraded_reasons=list(build.degraded_reasons or []),
            payload=dict(event.payload),
            created_at=event.created_at,
        )
