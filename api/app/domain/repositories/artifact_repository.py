#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, List, Optional

from app.domain.models.artifact import Artifact


class ArtifactRepository(Protocol):
    async def save(self, artifact: Artifact) -> None: ...

    async def get_by_id(self, artifact_id: str) -> Optional[Artifact]: ...

    async def list_by_session(self, session_id: str) -> List[Artifact]: ...

    async def get_by_share_token(self, token: str) -> Optional[Artifact]: ...
