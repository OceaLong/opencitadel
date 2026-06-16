#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import random
import secrets
import string
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from app.application.services.room.prompts import DARE_PROMPTS, TRUTH_PROMPTS
from app.domain.models.room import (
    Room,
    RoomEvent,
    RoomEventType,
    RoomParticipant,
    RoomStatus,
    RoomTodPrompt,
    TodMode,
)
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.storage.redis import RedisClient

logger = logging.getLogger(__name__)

ONLINE_THRESHOLD_SECONDS = 45


def _room_code(length: int = 8) -> str:
    alphabet = string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


_MAX_CODE_ATTEMPTS = 8


class RoomService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            redis_client: RedisClient,
    ) -> None:
        self._uow_factory = uow_factory
        self._redis = redis_client

    async def _publish(
            self,
            code: str,
            event: dict,
            *,
            include_snapshot: bool = False,
    ) -> None:
        payload = dict(event)
        if include_snapshot:
            payload["room"] = await self.get_room_snapshot(code)
        try:
            channel = f"room:{code.upper()}"
            await self._redis.client.publish(channel, json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.warning("房间事件广播失败: %s", exc)

    async def _require_room_participant(
            self,
            uow: IUnitOfWork,
            code: str,
            participant_id: str,
    ) -> Tuple[Room, RoomParticipant]:
        room = await uow.room.get_room_by_code(code)
        if not room:
            raise ValueError("房间不存在")
        participant = await uow.room.get_participant(participant_id)
        if not participant or participant.room_id != room.id:
            raise ValueError("参与者不存在或不属于该房间")
        return room, participant

    async def subscribe_events(self, code: str) -> AsyncGenerator[dict, None]:
        snapshot = await self.get_room_snapshot(code)
        yield {"event": "snapshot", "data": {"room": snapshot}}

        pubsub = self._redis.client.pubsub()
        channel = f"room:{code.upper()}"
        await pubsub.subscribe(channel)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                if message and message.get("type") == "message":
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield {"event": "room_event", "data": data}
                else:
                    yield {"event": "ping", "data": "{}"}
                await asyncio.sleep(0.05)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def _record_event(
            self,
            uow: IUnitOfWork,
            room_id: str,
            event_type: RoomEventType,
            payload: Dict[str, Any],
    ) -> RoomEvent:
        event = RoomEvent(
            id=str(uuid4()),
            room_id=room_id,
            type=event_type,
            payload=payload,
            created_at=datetime.utcnow(),
        )
        await uow.room.save_event(event)
        return event

    async def create_room(
            self,
            name: str,
            host_name: str,
            tod_mode: str = "random",
    ) -> dict:
        now = datetime.utcnow()
        room_id = str(uuid4())
        host_id = str(uuid4())
        code = await self._allocate_room_code()

        room = Room(
            id=room_id,
            code=code,
            name=name.strip() or "游戏房间",
            host_participant_id=host_id,
            tod_mode=TodMode(tod_mode if tod_mode in ("random", "custom") else "random"),
            turn_order=[host_id],
            current_turn_index=0,
            status=RoomStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        host = RoomParticipant(
            id=host_id,
            room_id=room_id,
            name=host_name.strip() or "房主",
            joined_at=now,
            last_seen=now,
        )

        async with self._uow_factory() as uow:
            await uow.room.save_room(room)
            await uow.room.save_participant(host)
            event = await self._record_event(
                uow,
                room_id,
                RoomEventType.JOIN,
                {"participant_id": host_id, "name": host.name},
            )

        snapshot = await self.get_room_snapshot(code)
        await self._publish(
            code,
            {"type": event.type.value, "payload": event.payload, "event_id": event.id},
            include_snapshot=True,
        )
        return {"room": snapshot, "participant_id": host_id}

    async def join_room(self, code: str, name: str) -> dict:
        now = datetime.utcnow()
        participant_id = str(uuid4())

        async with self._uow_factory() as uow:
            room = await uow.room.get_room_by_code(code)
            if not room:
                raise ValueError("房间不存在")
            if room.status != RoomStatus.ACTIVE:
                raise ValueError("房间已关闭")

            participant = RoomParticipant(
                id=participant_id,
                room_id=room.id,
                name=name.strip() or "玩家",
                joined_at=now,
                last_seen=now,
            )
            await uow.room.save_participant(participant)

            room.turn_order = list(room.turn_order) + [participant_id]
            room.updated_at = now
            await uow.room.save_room(room)

            event = await self._record_event(
                uow,
                room.id,
                RoomEventType.JOIN,
                {"participant_id": participant_id, "name": participant.name},
            )

        snapshot = await self.get_room_snapshot(code)
        await self._publish(
            code,
            {"type": event.type.value, "payload": event.payload, "event_id": event.id},
            include_snapshot=True,
        )
        return {"room": snapshot, "participant_id": participant_id}

    async def heartbeat(self, code: str, participant_id: str) -> dict:
        now = datetime.utcnow()
        async with self._uow_factory() as uow:
            room = await uow.room.get_room_by_code(code)
            if not room:
                raise ValueError("房间不存在")
            participant = await uow.room.get_participant(participant_id)
            if not participant or participant.room_id != room.id:
                raise ValueError("参与者不存在")
            await uow.room.update_participant_last_seen(participant_id, now)
        return {"ok": True}

    async def roll_dice(
            self,
            code: str,
            participant_id: str,
            dice_count: int = 1,
            dice_faces: int = 6,
    ) -> dict:
        dice_count = max(1, min(dice_count, 6))
        dice_faces = max(2, min(dice_faces, 20))
        results = [random.randint(1, dice_faces) for _ in range(dice_count)]

        async with self._uow_factory() as uow:
            room, participant = await self._require_room_participant(uow, code, participant_id)

            payload = {
                "participant_id": participant_id,
                "participant_name": participant.name,
                "results": results,
                "dice_count": dice_count,
                "dice_faces": dice_faces,
                "total": sum(results),
            }
            event = await self._record_event(uow, room.id, RoomEventType.DICE, payload)

        await self._publish(code, {
            "type": event.type.value,
            "payload": payload,
            "event_id": event.id,
        })
        return {"results": results, "total": sum(results), "event_id": event.id}

    async def draw_tod(
            self,
            code: str,
            participant_id: str,
            category: Optional[str] = None,
    ) -> dict:
        cat = category if category in ("truth", "dare") else random.choice(["truth", "dare"])

        async with self._uow_factory() as uow:
            room, participant = await self._require_room_participant(uow, code, participant_id)

            prompts: List[str] = []
            if room.tod_mode == TodMode.CUSTOM:
                custom = await uow.room.list_tod_prompts(room.id, cat)
                prompts = [p.text for p in custom]
            if not prompts:
                prompts = TRUTH_PROMPTS if cat == "truth" else DARE_PROMPTS

            text = random.choice(prompts)
            current_name = await self._current_player_name(room, uow)

            payload = {
                "participant_id": participant_id,
                "participant_name": participant.name,
                "category": cat,
                "text": text,
                "current_turn_name": current_name,
            }
            event = await self._record_event(uow, room.id, RoomEventType.TOD_DRAW, payload)

        await self._publish(code, {
            "type": event.type.value,
            "payload": payload,
            "event_id": event.id,
        })
        return {"category": cat, "text": text, "event_id": event.id}

    async def next_turn(self, code: str, participant_id: str) -> dict:
        async with self._uow_factory() as uow:
            room, participant = await self._require_room_participant(uow, code, participant_id)
            if not room.turn_order:
                raise ValueError("房间暂无参与者")
            current_turn_id = room.turn_order[room.current_turn_index]
            if participant_id not in {room.host_participant_id, current_turn_id}:
                raise ValueError("仅房主或当前回合玩家可切换回合")

            next_index = (room.current_turn_index + 1) % len(room.turn_order)
            room.current_turn_index = next_index
            room.updated_at = datetime.utcnow()
            await uow.room.save_room(room)

            current_id = room.turn_order[next_index]
            current = await uow.room.get_participant(current_id)
            payload = {
                "participant_id": participant_id,
                "current_turn_index": next_index,
                "current_turn_id": current_id,
                "current_turn_name": current.name if current else "未知",
            }
            event = await self._record_event(uow, room.id, RoomEventType.TURN, payload)

        await self._publish(code, {
            "type": event.type.value,
            "payload": payload,
            "event_id": event.id,
        })
        return payload

    async def add_custom_prompt(
            self,
            code: str,
            participant_id: str,
            category: str,
            text: str,
    ) -> dict:
        if category not in ("truth", "dare"):
            raise ValueError("类别必须为 truth 或 dare")
        if not text.strip():
            raise ValueError("题目不能为空")

        async with self._uow_factory() as uow:
            room, _ = await self._require_room_participant(uow, code, participant_id)
            if room.host_participant_id != participant_id:
                raise ValueError("仅房主可添加自定义题目")

            prompt = RoomTodPrompt(
                id=str(uuid4()),
                room_id=room.id,
                category=category,
                text=text.strip(),
                created_by=participant_id,
            )
            await uow.room.save_tod_prompt(prompt)
            room.tod_mode = TodMode.CUSTOM
            room.updated_at = datetime.utcnow()
            await uow.room.save_room(room)

            payload = {"category": category, "text": text.strip(), "created_by": participant_id}
            event = await self._record_event(uow, room.id, RoomEventType.PROMPT_ADD, payload)

        await self._publish(code, {
            "type": event.type.value,
            "payload": payload,
            "event_id": event.id,
        })
        return {"id": prompt.id, "category": category, "text": text.strip()}

    async def send_reaction(
            self,
            code: str,
            participant_id: str,
            emoji: str,
    ) -> dict:
        allowed = {"👍", "😂", "🔥", "❤️", "🎉", "😱", "👏", "💯"}
        emoji = (emoji or "").strip()
        if emoji not in allowed:
            raise ValueError("不支持的表情")

        async with self._uow_factory() as uow:
            room, participant = await self._require_room_participant(uow, code, participant_id)
            payload = {
                "participant_id": participant_id,
                "participant_name": participant.name,
                "emoji": emoji,
            }
            event = await self._record_event(uow, room.id, RoomEventType.REACTION, payload)

        await self._publish(code, {
            "type": event.type.value,
            "payload": payload,
            "event_id": event.id,
        })
        return payload

    async def get_room_snapshot(self, code: str) -> dict:
        async with self._uow_factory() as uow:
            room = await uow.room.get_room_by_code(code)
            if not room:
                raise ValueError("房间不存在")
            participants = await uow.room.list_participants(room.id)
            events = await uow.room.list_events(room.id, limit=30)

        now = datetime.utcnow()
        threshold = now - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)

        participant_dicts = []
        for p in participants:
            participant_dicts.append({
                "id": p.id,
                "name": p.name,
                "joined_at": p.joined_at.isoformat() if p.joined_at else None,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "online": p.last_seen >= threshold if p.last_seen else False,
            })

        current_turn_id = None
        current_turn_name = None
        if room.turn_order and 0 <= room.current_turn_index < len(room.turn_order):
            current_turn_id = room.turn_order[room.current_turn_index]
            for p in participant_dicts:
                if p["id"] == current_turn_id:
                    current_turn_name = p["name"]
                    break

        event_dicts = [
            {
                "id": e.id,
                "type": e.type.value,
                "payload": e.payload,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in reversed(events)
        ]

        return {
            "id": room.id,
            "code": room.code,
            "name": room.name,
            "host_participant_id": room.host_participant_id,
            "tod_mode": room.tod_mode.value,
            "turn_order": room.turn_order,
            "current_turn_index": room.current_turn_index,
            "current_turn_id": current_turn_id,
            "current_turn_name": current_turn_name,
            "status": room.status.value,
            "participants": participant_dicts,
            "recent_events": event_dicts,
        }

    async def _allocate_room_code(self) -> str:
        for _ in range(_MAX_CODE_ATTEMPTS):
            candidate = _room_code()
            async with self._uow_factory() as uow:
                existing = await uow.room.get_room_by_code(candidate)
            if not existing:
                return candidate
        raise ValueError("房间码生成失败，请稍后重试")

    async def _current_player_name(self, room: Room, uow: IUnitOfWork) -> Optional[str]:
        if not room.turn_order:
            return None
        idx = room.current_turn_index
        if idx < 0 or idx >= len(room.turn_order):
            return None
        pid = room.turn_order[idx]
        p = await uow.room.get_participant(pid)
        return p.name if p else None
