#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.services.agents.react import render_plan_snapshot
from app.domain.services.flows.planner_react import PlannerReActFlow


def test_render_plan_snapshot_marks_current_step():
    plan = Plan(
        language="zh",
        steps=[
            Step(description="done step", status=ExecutionStatus.COMPLETED),
            Step(id="current", description="running step", status=ExecutionStatus.RUNNING),
            Step(description="pending step", status=ExecutionStatus.PENDING),
        ],
    )
    snapshot = render_plan_snapshot(plan, "current")
    assert "[x] done step" in snapshot
    assert "[>] running step" in snapshot
    assert "[ ] pending step" in snapshot


def test_get_next_parallel_batch():
    plan = Plan(steps=[
        Step(description="a", parallelizable=True),
        Step(description="b", parallelizable=True),
        Step(description="c", parallelizable=False),
    ])
    batch = plan.get_next_parallel_batch()
    assert len(batch) == 2
    assert batch[0].description == "a"
    assert batch[1].description == "b"

    plan.steps[0].status = ExecutionStatus.COMPLETED
    plan.steps[1].status = ExecutionStatus.COMPLETED
    batch2 = plan.get_next_parallel_batch()
    assert len(batch2) == 1
    assert batch2[0].description == "c"


def test_parallel_update_step_summarizes_batch_without_none_step():
    steps = [
        Step(description="search a", status=ExecutionStatus.COMPLETED, success=True, result="A done"),
        Step(description="search b", status=ExecutionStatus.COMPLETED, success=True, result="B done"),
    ]
    update_step = PlannerReActFlow._build_parallel_update_step(steps)
    assert update_step.id == "parallel-batch"
    assert update_step.success is True
    assert "search a" in (update_step.result or "")
    assert "A done" in (update_step.result or "")
