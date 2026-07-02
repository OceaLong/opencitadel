#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.artifact import Artifact
from app.domain.repositories.artifact_repository import ArtifactRepository
from app.infrastructure.models.delivery_artifact import DeliveryArtifactModel


class DBArtifactRepository(ArtifactRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, artifact: Artifact) -> None:
        existing = await self.db_session.get(DeliveryArtifactModel, artifact.id)
        if existing:
            existing.update_from_domain(artifact)
        else:
            self.db_session.add(DeliveryArtifactModel.from_domain(artifact))

    async def get_by_id(self, artifact_id: str) -> Optional[Artifact]:
        row = await self.db_session.get(DeliveryArtifactModel, artifact_id)
        return row.to_domain() if row else None

    async def list_by_session(self, session_id: str) -> List[Artifact]:
        stmt = (
            select(DeliveryArtifactModel)
            .where(DeliveryArtifactModel.session_id == session_id)
            .order_by(DeliveryArtifactModel.updated_at.desc())
        )
        result = await self.db_session.execute(stmt)
        return [row.to_domain() for row in result.scalars().all()]

    async def get_by_share_token(self, token: str) -> Optional[Artifact]:
        stmt = select(DeliveryArtifactModel).where(DeliveryArtifactModel.share_token == token)
        result = await self.db_session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.to_domain() if row else None
