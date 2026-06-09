#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional, Protocol

from app.domain.models.room import Room, RoomEvent, RoomParticipant, RoomTodPrompt


class RoomRepository(Protocol):
    async def save_room(self, room: Room) -> None:
        ...

    async def get_room_by_code(self, code: str) -> Optional[Room]:
        ...

    async def get_room_by_id(self, room_id: str) -> Optional[Room]:
        ...

    async def save_participant(self, participant: RoomParticipant) -> None:
        ...

    async def get_participant(self, participant_id: str) -> Optional[RoomParticipant]:
        ...

    async def list_participants(self, room_id: str) -> List[RoomParticipant]:
        ...

    async def update_participant_last_seen(self, participant_id: str, last_seen) -> None:
        ...

    async def save_event(self, event: RoomEvent) -> None:
        ...

    async def list_events(self, room_id: str, limit: int = 50) -> List[RoomEvent]:
        ...

    async def save_tod_prompt(self, prompt: RoomTodPrompt) -> None:
        ...

    async def list_tod_prompts(self, room_id: str, category: Optional[str] = None) -> List[RoomTodPrompt]:
        ...
