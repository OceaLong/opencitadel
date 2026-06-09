#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.application.services.a2a_server_service import A2AServerService
from app.interfaces.service_dependencies import (
    get_a2a_server_service,
)

logger = logging.getLogger(__name__)

well_known_router = APIRouter(tags=["A2A Server"])
a2a_router = APIRouter(tags=["A2A Server"])


@well_known_router.get("/.well-known/agent-card.json")
async def get_agent_card(
        request: Request,
        a2a_server_service: A2AServerService = Depends(get_a2a_server_service),
) -> Dict[str, Any]:
    base_url = str(request.base_url).rstrip("/")
    return await a2a_server_service.build_agent_card(base_url)


@a2a_router.post("")
async def a2a_jsonrpc(
        request: Request,
        payload: Dict[str, Any],
        a2a_server_service: A2AServerService = Depends(get_a2a_server_service),
):
    method = payload.get("method")
    if method == "message/send":
        return await a2a_server_service.handle_message_send(payload)
    if method == "message/stream":
        async def event_generator():
            async for chunk in a2a_server_service.stream_message_events(payload):
                yield ServerSentEvent(data=chunk)

        return EventSourceResponse(event_generator())

    request_id = payload.get("id")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"不支持的方法: {method}"},
    }
