#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.checkpoint import Checkpoint, CheckpointAnchorType
from app.domain.repositories.checkpoint_repository import CheckpointRepository
from app.infrastructure.models.session_checkpoint import SessionCheckpointModel


class DBCheckpointRepository(CheckpointRepository):
    """PostgreSQL checkpoint repository."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save(self, checkpoint: Checkpoint) -> None:
        record = SessionCheckpointModel.from_domain(checkpoint)
        self.db_session.add(record)

    async def get_by_id(self, checkpoint_id: str) -> Optional[Checkpoint]:
        stmt = select(SessionCheckpointModel).where(SessionCheckpointModel.id == checkpoint_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return record.to_domain()

    async def list_by_session(self, session_id: str) -> List[Checkpoint]:
        stmt = (
            select(
                SessionCheckpointModel.id,
                SessionCheckpointModel.session_id,
                SessionCheckpointModel.anchor_type,
                SessionCheckpointModel.anchor_event_id,
                SessionCheckpointModel.label,
                SessionCheckpointModel.created_at,
            )
            .where(SessionCheckpointModel.session_id == session_id)
            .order_by(SessionCheckpointModel.created_at.asc())
        )
        result = await self.db_session.execute(stmt)
        return [
            Checkpoint(
                id=row.id,
                session_id=row.session_id,
                anchor_type=CheckpointAnchorType(row.anchor_type),
                anchor_event_id=row.anchor_event_id,
                label=row.label or "",
                created_at=row.created_at,
            )
            for row in result.all()
        ]

    async def delete_from(self, session_id: str, from_created_at: datetime, inclusive: bool = True) -> None:
        condition = (
            SessionCheckpointModel.created_at >= from_created_at
            if inclusive
            else SessionCheckpointModel.created_at > from_created_at
        )
        stmt = (
            delete(SessionCheckpointModel)
            .where(SessionCheckpointModel.session_id == session_id)
            .where(condition)
        )
        await self.db_session.execute(stmt)

    async def delete_by_session(self, session_id: str) -> None:
        stmt = delete(SessionCheckpointModel).where(SessionCheckpointModel.session_id == session_id)
        await self.db_session.execute(stmt)
