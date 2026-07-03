#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.event import ErrorEvent, MessageEvent
from app.domain.models.plan import ExecutionStatus, Plan, Step
from app.domain.models.session import Session, SessionStatus
from app.domain.services.flows.base import FlowStatus
from app.domain.services.flows.planner_react import _should_resume_failed_plan


def test_should_resume_failed_plan_when_session_failed():
    session = Session(id="s1", status=SessionStatus.FAILED)
    plan = Plan(steps=[Step(description="step 1")])
    assert _should_resume_failed_plan(session, plan) is True


def test_should_resume_failed_plan_when_recent_error_event():
    session = Session(
        id="s1",
        status=SessionStatus.RUNNING,
        events=[
            MessageEvent(role="user", message="plan a trip"),
            ErrorEvent(error="boom"),
        ],
    )
    plan = Plan(steps=[Step(description="step 1")])
    assert _should_resume_failed_plan(session, plan) is True


def test_should_not_resume_without_plan_or_error():
    session = Session(
        id="s1",
        status=SessionStatus.RUNNING,
        events=[MessageEvent(role="user", message="hello")],
    )
    assert _should_resume_failed_plan(session, None) is False
    assert _should_resume_failed_plan(session, Plan()) is False


def test_resume_sets_executing_when_pending_steps_exist():
    plan = Plan(
        steps=[
            Step(description="done", status=ExecutionStatus.COMPLETED),
            Step(description="pending"),
        ],
    )
    session = Session(id="s1", status=SessionStatus.FAILED)
    assert _should_resume_failed_plan(session, plan) is True
    assert plan.get_next_step() is not None

    status = FlowStatus.EXECUTING if plan.get_next_step() is not None else FlowStatus.SUMMARIZING
    assert status == FlowStatus.EXECUTING
