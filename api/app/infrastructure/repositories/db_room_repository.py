#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.room import Room, RoomEvent, RoomParticipant, RoomTodPrompt
from app.domain.repositories.room_repository import RoomRepository
from app.infrastructure.models.room import (
    RoomEventModel,
    RoomModel,
    RoomParticipantModel,
    RoomTodPromptModel,
)


class DBRoomRepository(RoomRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save_room(self, room: Room) -> None:
        stmt = select(RoomModel).where(RoomModel.id == room.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(RoomModel.from_domain(room))
            return
        record.name = room.name
        record.host_participant_id = room.host_participant_id
        record.tod_mode = room.tod_mode.value
        record.turn_order = room.turn_order
        record.current_turn_index = room.current_turn_index
        record.status = room.status.value
        record.updated_at = room.updated_at

    async def get_room_by_code(self, code: str) -> Optional[Room]:
        stmt = select(RoomModel).where(RoomModel.code == code.upper())
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_room_by_id(self, room_id: str) -> Optional[Room]:
        stmt = select(RoomModel).where(RoomModel.id == room_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def save_participant(self, participant: RoomParticipant) -> None:
        stmt = select(RoomParticipantModel).where(RoomParticipantModel.id == participant.id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            self.db_session.add(
                RoomParticipantModel(
                    id=participant.id,
                    room_id=participant.room_id,
                    name=participant.name,
                    joined_at=participant.joined_at,
                    last_seen=participant.last_seen,
                )
            )
            return
        record.name = participant.name
        record.last_seen = participant.last_seen

    async def get_participant(self, participant_id: str) -> Optional[RoomParticipant]:
        stmt = select(RoomParticipantModel).where(RoomParticipantModel.id == participant_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_participants(self, room_id: str) -> List[RoomParticipant]:
        stmt = (
            select(RoomParticipantModel)
            .where(RoomParticipantModel.room_id == room_id)
            .order_by(RoomParticipantModel.joined_at.asc())
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def update_participant_last_seen(self, participant_id: str, last_seen: datetime) -> None:
        await self.db_session.execute(
            update(RoomParticipantModel)
            .where(RoomParticipantModel.id == participant_id)
            .values(last_seen=last_seen)
        )

    async def save_event(self, event: RoomEvent) -> None:
        self.db_session.add(
            RoomEventModel(
                id=event.id,
                room_id=event.room_id,
                type=event.type.value,
                payload=event.payload,
                created_at=event.created_at,
            )
        )

    async def list_events(self, room_id: str, limit: int = 50) -> List[RoomEvent]:
        stmt = (
            select(RoomEventModel)
            .where(RoomEventModel.room_id == room_id)
            .order_by(RoomEventModel.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        result = await self.db_session.execute(stmt)
        return [r.to_domain() for r in result.scalars().all()]

    async def save_tod_prompt(self, prompt: RoomTodPrompt) -> None:
        self.db_session.add(
            RoomTodPromptModel(
                id=prompt.id,
                room_id=prompt.room_id,
                category=prompt.category,
                text=prompt.text,
                created_by=prompt.created_by,
            )
        )

    async def list_tod_prompts(
            self,
            room_id: str,
            category: Optional[str] = None,
    ) -> List[RoomTodPrompt]:
        stmt = select(RoomTodPromptModel).where(RoomTodPromptModel.room_id == room_id)
        if category:
            stmt = stmt.where(RoomTodPromptModel.category == category)
        result = await self.db_session.execute(stmt)
        return [
            RoomTodPrompt(
                id=r.id,
                room_id=r.room_id,
                category=r.category,
                text=r.text,
                created_by=r.created_by,
            )
            for r in result.scalars().all()
        ]
