#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event import (
    MessageDeltaEvent,
    MessageEvent,
    AssistantNoticeEvent,
    DebugItemEvent,
    PlanEvent,
    PlanEventStatus,
)
from app.domain.models.event_policy import should_persist_event, is_user_timeline_event
from app.domain.models.event_policy import should_project_event
from app.domain.models.plan import Plan, Step


def test_transient_delta_should_not_persist():
    event = MessageDeltaEvent(stream_id="s1", delta='{"title":"x"}')
    assert should_persist_event(event) is False
    assert is_user_timeline_event(event) is False


def test_user_message_should_persist_and_show_in_timeline():
    event = MessageEvent(role="user", message="hello")
    assert should_persist_event(event) is True
    assert is_user_timeline_event(event) is True


def test_assistant_notice_should_persist_and_show_in_timeline():
    event = AssistantNoticeEvent(message="我已制定计划，开始执行。")
    assert should_persist_event(event) is True
    assert is_user_timeline_event(event) is True


def test_debug_item_should_persist_but_not_timeline():
    event = DebugItemEvent(item_type="planner_output", payload={"title": "t"})
    assert should_persist_event(event) is True
    assert is_user_timeline_event(event) is False


def test_plan_event_should_persist_but_not_timeline():
    event = PlanEvent(
        plan=Plan(title="t", goal="g", steps=[Step(description="s1")]),
        status=PlanEventStatus.CREATED,
    )
    assert should_persist_event(event) is True
    assert is_user_timeline_event(event) is False


def test_projection_allows_user_message_and_message_delta():
    assert should_project_event(MessageEvent(role="assistant", message="hi")) is True
    assert should_project_event(MessageDeltaEvent(stream_id="s1", delta="hi")) is True


def test_projection_hides_debug_by_default_and_allows_when_requested():
    event = DebugItemEvent(item_type="planner_output", payload={"title": "t"})
    assert should_project_event(event) is False
    assert should_project_event(event, include_debug=True) is True
