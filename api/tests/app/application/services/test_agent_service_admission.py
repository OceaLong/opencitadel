from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.application.execution.public_projection import (
    PublicEventPage,
    PublicExecutionEvent,
)
from app.application.services.agent_service import AgentService
from app.domain.models.file import File
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.models.skill import Skill, SkillAgentParams


class SessionRepository:
    def __init__(self, session):
        self.session = session
        self.config_updates = []
        self.files = []

    async def get_by_id(self, session_id, scope=None):
        return self.session if session_id == self.session.id else None

    async def lock_by_id(self, session_id, scope=None):
        return await self.get_by_id(session_id, scope=scope)

    async def update_session_config(self, session_id, **values):
        self.config_updates.append((session_id, values))

    async def update_latest_message(self, *_):
        return None

    async def save(self, session):
        self.session = session

    async def add_file(self, session_id, file):
        self.files.append((session_id, file))


class UnitOfWork:
    def __init__(self, session, skill, files=()):
        self.session = SessionRepository(session)
        self.skill = SimpleNamespace(get_by_id=self.get_skill)
        self.execution_commands = object()
        self._skill = skill
        self._files = {file.id: file for file in files}
        self.file = SimpleNamespace(get_by_id=self.get_file)

    async def get_skill(self, skill_id, scope=None):
        return self._skill if self._skill and self._skill.id == skill_id else None

    async def get_file(self, file_id, scope=None):
        return self._files.get(file_id)

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, *_):
        return None


class Admission:
    def __init__(self):
        self.calls = []

    async def admit(self, **kwargs):
        self.calls.append(kwargs)
        return UUID("10000000-0000-0000-0000-000000000001")


class Projection:
    def __init__(self, admission):
        self.admission = admission

    async def list_events(self, **_):
        if not self.admission.calls:
            return PublicEventPage(events=(), next_cursor=None, prev_cursor=None, has_earlier=False)
        event = PublicExecutionEvent(
            cursor="cursor-1",
            event_id=UUID("20000000-0000-0000-0000-000000000001"),
            event_type="done",
            run_id=UUID("10000000-0000-0000-0000-000000000001"),
            stream_id="session-1",
            stream_version=1,
            payload={"status": "completed"},
            occurred_at=datetime.now(UTC),
        )
        return PublicEventPage(
            events=(event,), next_cursor=None, prev_cursor=None, has_earlier=False
        )


@pytest.mark.asyncio
async def test_skill_profile_is_frozen_into_run_admission():
    session = Session(id="session-1", owner_user_id="user-1")
    skill = Skill(
        id="skill-1",
        name="Auditor",
        slug="auditor",
        recommended_model_id="model-recommended",
        agent_params=SkillAgentParams(
            max_iterations=25,
            max_retries=4,
            temperature_override=0.1,
        ),
    )
    uow = UnitOfWork(session, skill)
    admission = Admission()
    service = AgentService(
        uow_factory=lambda: uow,
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=Projection(admission),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )

    events = [
        event
        async for event in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            message="audit",
            request_id=uuid4(),
            skill_id="skill-1",
        )
    ]

    assert [event.event_type for event in events] == ["done"]
    call = admission.calls[0]
    assert call["private_input"]["skill_id"] == "skill-1"
    assert call["private_input"]["model_id"] == "model-recommended"
    assert call["private_input"]["temperature_override"] == 0.1
    assert "memory_context" not in call["private_input"]
    assert "workflow" not in call


@pytest.mark.asyncio
async def test_attachments_are_owner_checked_frozen_and_bound_to_session():
    session = Session(id="session-1", owner_user_id="user-1")
    file = File(
        id="file-1",
        filename="../Quarterly report.pdf",
        mime_type="application/pdf",
        size=1234,
        owner_user_id="user-1",
    )
    uow = UnitOfWork(session, None, files=(file,))
    admission = Admission()
    service = AgentService(
        uow_factory=lambda: uow,
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=Projection(admission),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )

    _ = [
        event
        async for event in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            message="summarize",
            request_id=uuid4(),
            attachments=["file-1"],
        )
    ]

    call = admission.calls[0]
    assert call["public_input"]["attachments"] == [
        {
            "file_id": "file-1",
            "filename": "../Quarterly report.pdf",
            "mime_type": "application/pdf",
            "size": 1234,
        }
    ]
    assert call["private_input"]["attachments"][0]["sandbox_path"] == (
        "/home/ubuntu/uploads/file-1-Quarterly_report.pdf"
    )
    assert uow.session.files == [("session-1", file)]


@pytest.mark.asyncio
async def test_attachments_without_a_message_are_rejected():
    session = Session(id="session-1", owner_user_id="user-1")
    file = File(
        id="file-1",
        filename="report.pdf",
        mime_type="application/pdf",
        size=1234,
        owner_user_id="user-1",
    )
    admission = Admission()
    service = AgentService(
        uow_factory=lambda: UnitOfWork(session, None, files=(file,)),
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=Projection(admission),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )

    with pytest.raises(ValueError, match="attachments require a message"):
        async for _ in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            attachments=["file-1"],
        ):
            pass


@pytest.mark.asyncio
async def test_direct_chat_admission_rejects_blank_message() -> None:
    session = Session(id="session-1", owner_user_id="user-1")
    admission = Admission()
    service = AgentService(
        uow_factory=lambda: UnitOfWork(session, None),
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=Projection(admission),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )

    with pytest.raises(ValueError, match="message must not be blank"):
        async for _ in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            message=" \n\t ",
            request_id=uuid4(),
        ):
            pass

    assert admission.calls == []


@pytest.mark.asyncio
async def test_direct_chat_resume_rejects_turn_request_id() -> None:
    session = Session(id="session-1", owner_user_id="user-1")
    admission = Admission()
    service = AgentService(
        uow_factory=lambda: UnitOfWork(session, None),
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=Projection(admission),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )

    with pytest.raises(
        ValueError,
        match="request_id is only valid when message is present",
    ):
        async for _ in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            request_id=uuid4(),
        ):
            pass

    assert admission.calls == []


@pytest.mark.asyncio
async def test_chat_request_id_deduplicates_admission_and_blocks_parallel_run():
    session = Session(id="session-1", owner_user_id="user-1")
    uow = UnitOfWork(session, None)
    admission = Admission()
    service = AgentService(
        uow_factory=lambda: uow,
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=Projection(admission),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
        idle_timeout_seconds=0.01,
    )
    request_id = UUID("30000000-0000-0000-0000-000000000001")

    for _ in range(2):
        _ = [
            event
            async for event in service.chat(
                "session-1",
                owner_scope=OwnerScope.personal("user-1"),
                message="one logical turn",
                request_id=request_id,
            )
        ]

    assert len(admission.calls) == 1
    assert admission.calls[0]["idempotency_key"] == (f"session:session-1:request:{request_id}")
    assert session.active_execution_request_id == request_id
    assert session.active_execution_run_id == UUID("10000000-0000-0000-0000-000000000001")

    with pytest.raises(ValueError, match="already has an active Run"):
        async for _ in service.chat(
            "session-1",
            owner_scope=OwnerScope.personal("user-1"),
            message="parallel turn",
            request_id=uuid4(),
        ):
            pass
