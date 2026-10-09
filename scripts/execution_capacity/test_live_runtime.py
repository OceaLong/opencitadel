"""Pure protocol tests; no live resources opened."""

import pytest


def test_window_plan_is_fixed_bounded_and_has_ten_unique_fresh_sessions():
    from scripts.execution_capacity.live_runtime import window_plan

    plan = {
        "startup_seconds": 10,
        "seconds": 2,
        "sessions": [{"session_id": str(i)} for i in range(10)],
    }
    assert window_plan(plan, 100)[0] == 10_000_000_100
    for field, value in [
        ("seconds", 0),
        ("seconds", 31),
        ("startup_seconds", 0),
        ("sessions", plan["sessions"][:9]),
        ("sessions", [plan["sessions"][0]] * 10),
    ]:
        with pytest.raises(
            ValueError,
            match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
        ):
            window_plan({**plan, field: value}, 100)


def test_batch_load_requires_actual_new_dispatches_and_settlements():
    from scripts.execution_capacity.live_runtime import validate_batch_window

    settings = {"subject_concurrency": 5, "judge_concurrency": 2, "environment_concurrency": 2}
    a = {"status": "running", "settings": settings, "sends": 10, "settled": 1}
    b = {**a, "sends": 11, "settled": 2}
    validate_batch_window(a, b, settings)
    for changed in [
        {**b, "status": "completed"},
        {**b, "sends": 10},
        {**b, "settled": 1},
        {**b, "settings": {**settings, "subject_concurrency": 10}},
    ]:
        with pytest.raises(
            ValueError,
            match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
        ):
            validate_batch_window(a, changed, settings)


def test_bound_sessions_cannot_override_deployment_authority():
    from scripts.execution_capacity.live_runtime import session_binding

    base = {"principal_id": "owner", "database_name": "owned"}
    legitimate = {
        "session_id": "session",
        "session_created_at": "date",
        "model_id": "model",
        "endpoint_id": "endpoint",
    }
    assert session_binding(base, legitimate)["principal_id"] == "owner"
    with pytest.raises(
        ValueError,
        match=r"live|headroom|stream|terminal|cohort|policy|duplicate|progress|window|authority|batch|handler",
    ):
        session_binding(base, {**legitimate, "principal_id": "foreign"})


@pytest.mark.asyncio
async def test_admission_uses_actual_agent_service_ask_and_reads_attached_run(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from uuid import uuid4

    from scripts.execution_capacity import live_runtime
    from scripts.execution_capacity.observers import RecoveryJournal

    from app.application.services.agent_service import AgentService
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.session import Session
    from app.domain.models.session_mode import SessionMode
    from tests.app.application.services.test_agent_service_admission import (
        Admission,
        Projection,
        UnitOfWork,
    )
    from tests.app.execution_test_support import run_execution_context_for
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    session = Session(
        id="session-1", owner_user_id="user-1", mode=SessionMode.ASK, model_id="model"
    )
    uow = UnitOfWork(session, None)
    admission = Admission()
    agent = AgentService(
        uow_factory=lambda: uow,
        admission_service=admission,
        command_ingress=None,
        public_projection=Projection(admission),
        run_projection=None,
    )
    scope = OwnerScope.personal("user-1")
    authorization = AuthorizationContext.for_principal(Principal(user_id="user-1"), scope=scope)

    async def prerequisite(*args):
        return authorization, run_execution_context_for("ask").policy_snapshot

    monkeypatch.setattr(live_runtime, "verify_prerequisite", prerequisite)
    model = resolved_chat_model(model_name="acceptance-live", base_url="http://owned:8080/v1")
    model = model.model_copy(
        update={
            "model": model.model.model_copy(
                update={
                    "settings": model.model.settings.model_copy(update={"max_output_tokens": 4096})
                }
            )
        }
    )

    async def resolve(*args, **kwargs):
        return model

    workload = object.__new__(live_runtime.LiveWorkload)
    workload.binding = {"provider_endpoint": "http://owned:8080/v1"}
    workload.scope = scope
    workload.facts = SimpleNamespace(scope_key="user:user-1")
    workload.shared = SimpleNamespace(
        agent_service=agent,
        uow_factory=lambda *a: uow,
        inference_model_service=SimpleNamespace(resolve_chat=resolve),
    )
    workload.authorized = None
    workload.sessions = []
    workload.runs = []
    workload.tasks = []
    workload.boot = "unit"
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        workload.journal = journal
        identity = uuid4()
        receipt = await workload.admit(
            {
                "session_id": "session-1",
                "session_created_at": "date",
                "model_id": "model",
                "endpoint_id": "endpoint",
            },
            identity,
            profile="acceptance-live",
        )
        assert receipt["run_id"] == str(session.active_execution_run_id)
        assert receipt["before_ns"] <= receipt["after_ns"]
        assert admission.calls[0]["source_entity_type"] == "session"
        assert admission.calls[0]["family"].value == "ask"
        assert admission.calls[0]["private_input"]["mode"] == "ask"
        assert journal.get("live_admission", identity)["receipt"]["run_id"] == receipt["run_id"]
        await __import__("asyncio").gather(*workload.tasks)
