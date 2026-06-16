#!/usr/bin/env python
# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class RoomStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class TodMode(str, Enum):
    RANDOM = "random"
    CUSTOM = "custom"


class RoomEventType(str, Enum):
    JOIN = "join"
    LEAVE = "leave"
    DICE = "dice"
    TOD_DRAW = "tod_draw"
    TURN = "turn"
    PROMPT_ADD = "prompt_add"
    HEARTBEAT = "heartbeat"
    REACTION = "reaction"


@dataclass
class Room:
    id: str
    code: str
    name: str
    host_participant_id: str
    tod_mode: TodMode
    turn_order: List[str]
    current_turn_index: int
    status: RoomStatus
    created_at: datetime
    updated_at: datetime


@dataclass
class RoomParticipant:
    id: str
    room_id: str
    name: str
    joined_at: datetime
    last_seen: datetime


@dataclass
class RoomEvent:
    id: str
    room_id: str
    type: RoomEventType
    payload: Dict[str, Any]
    created_at: datetime


@dataclass
class RoomTodPrompt:
    id: str
    room_id: str
    category: str
    text: str
    created_by: Optional[str] = None
