#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RoomParticipantSchema(BaseModel):
    id: str
    name: str
    joined_at: Optional[str] = None
    last_seen: Optional[str] = None
    online: bool = False


class RoomEventSchema(BaseModel):
    id: str
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class RoomSnapshotSchema(BaseModel):
    id: str
    code: str
    name: str
    host_participant_id: str
    tod_mode: str
    turn_order: List[str] = Field(default_factory=list)
    current_turn_index: int = 0
    current_turn_id: Optional[str] = None
    current_turn_name: Optional[str] = None
    status: str
    participants: List[RoomParticipantSchema] = Field(default_factory=list)
    recent_events: List[RoomEventSchema] = Field(default_factory=list)


class CreateRoomRequest(BaseModel):
    name: str = "游戏房间"
    host_name: str
    tod_mode: str = "random"


class CreateRoomResponse(BaseModel):
    room: RoomSnapshotSchema
    participant_id: str


class JoinRoomRequest(BaseModel):
    name: str


class JoinRoomResponse(BaseModel):
    room: RoomSnapshotSchema
    participant_id: str


class HeartbeatRequest(BaseModel):
    participant_id: str


class RollDiceRequest(BaseModel):
    participant_id: str
    dice_count: int = 1
    dice_faces: int = 6


class RollDiceResponse(BaseModel):
    results: List[int]
    total: int
    event_id: str


class DrawTodRequest(BaseModel):
    participant_id: str
    category: Optional[str] = None


class DrawTodResponse(BaseModel):
    category: str
    text: str
    event_id: str


class NextTurnRequest(BaseModel):
    participant_id: str


class AddTodPromptRequest(BaseModel):
    participant_id: str
    category: str
    text: str
