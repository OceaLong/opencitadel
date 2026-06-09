#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.room import (
    Room,
    RoomEvent,
    RoomEventType,
    RoomParticipant,
    RoomStatus,
    RoomTodPrompt,
    TodMode,
)
from .base import Base


class RoomModel(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        Index("ix_rooms_code", "code", unique=True),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False, server_default=text("''"))
    host_participant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tod_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'random'"))
    turn_order: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    current_turn_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> Room:
        return Room(
            id=self.id,
            code=self.code,
            name=self.name,
            host_participant_id=self.host_participant_id,
            tod_mode=TodMode(self.tod_mode),
            turn_order=self.turn_order or [],
            current_turn_index=self.current_turn_index or 0,
            status=RoomStatus(self.status),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_domain(cls, room: Room) -> "RoomModel":
        return cls(
            id=room.id,
            code=room.code,
            name=room.name,
            host_participant_id=room.host_participant_id,
            tod_mode=room.tod_mode.value,
            turn_order=room.turn_order,
            current_turn_index=room.current_turn_index,
            status=room.status.value,
            created_at=room.created_at,
            updated_at=room.updated_at,
        )


class RoomParticipantModel(Base):
    __tablename__ = "room_participants"
    __table_args__ = (
        Index("ix_room_participants_room", "room_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    room_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> RoomParticipant:
        return RoomParticipant(
            id=self.id,
            room_id=self.room_id,
            name=self.name,
            joined_at=self.joined_at,
            last_seen=self.last_seen,
        )


class RoomEventModel(Base):
    __tablename__ = "room_events"
    __table_args__ = (
        Index("ix_room_events_room", "room_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    room_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> RoomEvent:
        return RoomEvent(
            id=self.id,
            room_id=self.room_id,
            type=RoomEventType(self.type),
            payload=self.payload or {},
            created_at=self.created_at,
        )


class RoomTodPromptModel(Base):
    __tablename__ = "room_tod_prompts"
    __table_args__ = (
        Index("ix_room_tod_prompts_room", "room_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    room_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
