#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.errors.exceptions import BadRequestError
from app.application.services.room_service import RoomService
from app.interfaces.schemas.base import Response
from app.interfaces.schemas.room import (
    AddTodPromptRequest,
    CreateRoomRequest,
    CreateRoomResponse,
    DrawTodRequest,
    DrawTodResponse,
    HeartbeatRequest,
    JoinRoomRequest,
    JoinRoomResponse,
    NextTurnRequest,
    RollDiceRequest,
    RollDiceResponse,
    RoomSnapshotSchema,
)
from app.interfaces.service_dependencies import get_room_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rooms", tags=["房间"])


@router.post("", response_model=Response[CreateRoomResponse])
async def create_room(
        request: CreateRoomRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[CreateRoomResponse]:
    try:
        data = await service.create_room(request.name, request.host_name, request.tod_mode)
        return Response.success(data=CreateRoomResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{code}/join", response_model=Response[JoinRoomResponse])
async def join_room(
        code: str,
        request: JoinRoomRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[JoinRoomResponse]:
    try:
        data = await service.join_room(code, request.name)
        return Response.success(data=JoinRoomResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/{code}", response_model=Response[RoomSnapshotSchema])
async def get_room(
        code: str,
        service: RoomService = Depends(get_room_service),
) -> Response[RoomSnapshotSchema]:
    try:
        data = await service.get_room_snapshot(code)
        return Response.success(data=RoomSnapshotSchema(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{code}/heartbeat", response_model=Response[dict])
async def heartbeat(
        code: str,
        request: HeartbeatRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[dict]:
    try:
        data = await service.heartbeat(code, request.participant_id)
        return Response.success(data=data)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{code}/dice", response_model=Response[RollDiceResponse])
async def roll_dice(
        code: str,
        request: RollDiceRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[RollDiceResponse]:
    try:
        data = await service.roll_dice(
            code,
            request.participant_id,
            dice_count=request.dice_count,
            dice_faces=request.dice_faces,
        )
        return Response.success(data=RollDiceResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{code}/tod", response_model=Response[DrawTodResponse])
async def draw_tod(
        code: str,
        request: DrawTodRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[DrawTodResponse]:
    try:
        data = await service.draw_tod(code, request.participant_id, request.category)
        return Response.success(data=DrawTodResponse(**data))
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{code}/turn", response_model=Response[dict])
async def next_turn(
        code: str,
        request: NextTurnRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[dict]:
    try:
        data = await service.next_turn(code, request.participant_id)
        return Response.success(data=data)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.post("/{code}/prompts", response_model=Response[dict])
async def add_prompt(
        code: str,
        request: AddTodPromptRequest,
        service: RoomService = Depends(get_room_service),
) -> Response[dict]:
    try:
        data = await service.add_custom_prompt(
            code,
            request.participant_id,
            request.category,
            request.text,
        )
        return Response.success(data=data)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/{code}/stream")
async def room_stream(
        code: str,
        service: RoomService = Depends(get_room_service),
) -> EventSourceResponse:
    """SSE 实时房间事件流"""

    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        try:
            async for item in service.subscribe_events(code):
                yield ServerSentEvent(event=item["event"], data=item["data"])
        except ValueError as exc:
            yield ServerSentEvent(event="error", data=json.dumps({"message": str(exc)}))
        except asyncio.CancelledError:
            logger.info("房间 SSE 连接关闭: %s", code)
            raise

    return EventSourceResponse(event_generator())
