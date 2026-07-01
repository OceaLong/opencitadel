#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio

from app.domain.models.event import SubAgentEventStatus
from app.domain.services.tools.subagent import SubAgentTool


def test_subagent_tool_drains_events_by_subagent_id():
    async def _run_subagent(*, goal: str, agent_name: str, allowed_tools=None) -> str:
        return f"done: {goal}"

    tool = SubAgentTool(run_subagent=_run_subagent, max_concurrency=2)

    async def _run():
        first, second = await asyncio.gather(
            tool.delegate_subtask("alpha"),
            tool.delegate_subtask("beta"),
        )
        first_id = first.data["subagent_id"]
        second_id = second.data["subagent_id"]

        first_events = tool.drain_events(subagent_id=first_id)
        assert {event.subagent_id for event in first_events} == {first_id}
        assert [event.status for event in first_events] == [
            SubAgentEventStatus.STARTED,
            SubAgentEventStatus.COMPLETED,
        ]

        second_events = tool.drain_events(subagent_id=second_id)
        assert {event.subagent_id for event in second_events} == {second_id}

    asyncio.run(_run())
