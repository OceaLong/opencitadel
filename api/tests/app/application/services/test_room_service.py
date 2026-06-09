#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.room_service import RoomService
from datetime import datetime

from app.domain.models.room import Room, RoomParticipant, RoomStatus, TodMode


class InMemoryRoomRepo:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self.participants: dict[str, RoomParticipant] = {}
        self.events: list = []
        self.prompts: list = []

    async def save_room(self, room: Room) -> None:
        self.rooms[room.id] = room

    async def get_room_by_code(self, code: str):
        for room in self.rooms.values():
            if room.code == code.upper():
                return room
        return None

    async def get_room_by_id(self, room_id: str):
        return self.rooms.get(room_id)

    async def save_participant(self, participant: RoomParticipant) -> None:
        self.participants[participant.id] = participant

    async def get_participant(self, participant_id: str):
        return self.participants.get(participant_id)

    async def list_participants(self, room_id: str):
        return [p for p in self.participants.values() if p.room_id == room_id]

    async def update_participant_last_seen(self, participant_id: str, last_seen) -> None:
        participant = self.participants[participant_id]
        participant.last_seen = last_seen

    async def save_event(self, event) -> None:
        self.events.append(event)

    async def list_events(self, room_id: str, limit: int = 50):
        return self.events[-limit:]

    async def save_tod_prompt(self, prompt) -> None:
        self.prompts.append(prompt)

    async def list_tod_prompts(self, room_id: str, category=None):
        return [p for p in self.prompts if p.room_id == room_id]


class FakeRedis:
    class _Client:
        async def publish(self, channel, message):
            return 1

        def pubsub(self):
            raise RuntimeError("not used in unit tests")

    client = _Client()


class FakeUow:
    def __init__(self, repo: InMemoryRoomRepo) -> None:
        self.room = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.fixture
def service():
    repo = InMemoryRoomRepo()
    return RoomService(uow_factory=lambda: FakeUow(repo), redis_client=FakeRedis()), repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_roll_dice_rejects_foreign_participant(service):
    svc, repo = service
    created = await svc.create_room("测试房", "房主")
    now = datetime.utcnow()
    other_room = Room(
        id="other-room",
        code="99999999",
        name="其他",
        host_participant_id="host-2",
        tod_mode=TodMode.RANDOM,
        turn_order=[],
        current_turn_index=0,
        status=RoomStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    await repo.save_room(other_room)
    foreign = RoomParticipant(
        id="foreign-id",
        room_id=other_room.id,
        name="外人",
        joined_at=created["room"]["participants"][0]["joined_at"],
        last_seen=created["room"]["participants"][0]["last_seen"],
    )
    await repo.save_participant(foreign)

    with pytest.raises(ValueError, match="不属于该房间"):
        await svc.roll_dice(created["room"]["code"], "foreign-id")


@pytest.mark.anyio
async def test_next_turn_requires_host_or_current_player(service):
    svc, _ = service
    created = await svc.create_room("测试房", "房主")
    joined = await svc.join_room(created["room"]["code"], "玩家B")
    host_id = created["participant_id"]
    guest_id = joined["participant_id"]

    with pytest.raises(ValueError, match="仅房主或当前回合玩家"):
        await svc.next_turn(created["room"]["code"], guest_id)

    result = await svc.next_turn(created["room"]["code"], host_id)
    assert "current_turn_index" in result
