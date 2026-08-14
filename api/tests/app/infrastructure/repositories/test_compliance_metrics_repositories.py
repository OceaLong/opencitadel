#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Real-SQL tests for the 7 repository methods Task 4 (合规评估器真化) added
to feed ComplianceService._collect_metrics. Mirrors the SQLite-backed
fixture pattern in test_db_tool_approval_repository.py (create_engine +
_AsyncSessionAdapter running the real DB<n>Repository classes against a
real, if lightweight, SQL engine) rather than hand-rolled fakes -- so a bug
in the actual SELECT/GROUP BY/ORDER BY/LIKE/IN clauses would fail these
tests, not just a Python-side stand-in.

Postgres-only DDL (``CURRENT_TIMESTAMP(0)`` defaults, ``::type`` casts,
FK constraints referencing tables outside this file's scope) do not parse
under SQLite, so each table is recreated on a throwaway ``MetaData`` with
those specifics stripped (see ``_shadow_table``); the *querying* code under
test is completely untouched -- it still runs through the real
``app.infrastructure.repositories.db_*_repository`` classes against
whatever table happens to be bound to that name on the connected engine.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.domain.models.codebase import SessionMode
from app.infrastructure.models.audit_log import AuditLogORM
from app.infrastructure.models.llm_endpoint import LLMEndpointORM
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.session_checkpoint import SessionCheckpointModel
from app.infrastructure.models.user import UserORM
from app.infrastructure.repositories.db_audit_repository import DBAuditRepository
from app.infrastructure.repositories.db_checkpoint_repository import DBCheckpointRepository
from app.infrastructure.repositories.db_llm_endpoint_repository import DBLLMEndpointRepository
from app.infrastructure.repositories.db_session_repository import DBSessionRepository
from app.infrastructure.repositories.db_user_repository import DBUserRepository


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _AsyncSessionAdapter:
    """Only adapts SQLAlchemy's local sync Session call shape to AsyncSession."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self) -> None:
        self._session.flush()


def _shadow_table(orm_cls, metadata: MetaData) -> Table:
    """Rebuild an ORM class's table (name + column names/types/nullability/
    primary keys only) on a throwaway MetaData, dropping ForeignKey
    constraints and server_default expressions.

    Why: the real tables use Postgres-only server_default SQL
    (``CURRENT_TIMESTAMP(0)``, ``'...'::character varying``) that SQLite's
    DDL parser rejects outright, and FKs that reference tables (teams,
    llm_models, ...) out of scope for these single-repository tests. None
    of that affects the SELECT/GROUP BY/ORDER BY/LIKE queries under test --
    this file supplies every column explicitly on insert instead of relying
    on DB-side defaults.
    """
    columns = [
        Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
        for c in orm_cls.__table__.columns
    ]
    return Table(orm_cls.__table__.name, metadata, *columns)


@pytest.fixture
def db():
    metadata = MetaData()
    for orm_cls in (AuditLogORM, UserORM, SessionModel, SessionCheckpointModel, LLMEndpointORM):
        _shadow_table(orm_cls, metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        adapter = _AsyncSessionAdapter(session)
        yield SimpleNamespace(
            session=session,
            audit=DBAuditRepository(adapter),
            user=DBUserRepository(adapter),
            checkpoint=DBCheckpointRepository(adapter),
            llm_endpoint=DBLLMEndpointRepository(adapter, cipher=None),
            session_repo=DBSessionRepository(adapter),
        )
        session.rollback()
    engine.dispose()


# --- fixture builders: every NOT NULL column is set explicitly since the
# shadow tables have no server-side defaults. ---

def _audit_log(
    *,
    id: str,
    action: str,
    created_at: datetime,
    chain_seq: int | None = None,
    metadata: dict | None = None,
) -> AuditLogORM:
    return AuditLogORM(
        id=id,
        actor_user_id=None,
        actor_ip="",
        action=action,
        resource_type="",
        resource_id="",
        team_id=None,
        request_id="",
        metadata_json=metadata or {},
        chain_seq=chain_seq,
        signing_key_id="primary",
        prev_hash=None,
        entry_hash=None,
        created_at=created_at,
    )


def _user(*, id: str, email: str, username: str, global_role: str) -> UserORM:
    return UserORM(
        id=id,
        email=email,
        username=username,
        password_hash=None,
        display_name="",
        avatar_url="",
        global_role=global_role,
        status="active",
        token_version=0,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        last_login_at=None,
    )


def _session_row(*, id: str, mode: str, created_at: datetime) -> SessionModel:
    return SessionModel(
        id=id,
        sandbox_id=None,
        task_id=None,
        title="",
        unread_message_count=0,
        latest_message="",
        latest_message_at=None,
        model_id=None,
        skill_id=None,
        thinking_enabled=False,
        codebase_id=None,
        knowledge_base_id=None,
        owner_user_id=None,
        team_id=None,
        mode=mode,
        pending_phase=None,
        pending_metadata=None,
        operator_scope=None,
        operator_domains=[],
        gate_profile=None,
        status="running",
        current_run_epoch_id=None,
        current_run_epoch_seq=None,
        current_run_terminal_status=None,
        updated_at=created_at,
        created_at=created_at,
    )


def _checkpoint(*, id: str, session_id: str, created_at: datetime) -> SessionCheckpointModel:
    return SessionCheckpointModel(
        id=id,
        session_id=session_id,
        anchor_type="step",
        anchor_event_id="ev-1",
        label="",
        seq_before=None,
        memories_snapshot={},
        files_snapshot=[],
        session_state={},
        sandbox_snapshot_key=None,
        browser_snapshot_key=None,
        created_at=created_at,
    )


def _llm_endpoint(*, id: str, base_url: str) -> LLMEndpointORM:
    return LLMEndpointORM(
        id=id,
        display_name="",
        provider="openai",
        base_url=base_url,
        api_key="",
        api_key_encryption="legacy_plaintext",
        owner_user_id=None,
        team_id=None,
        visibility="global",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


# --- AuditRepository.count_by_actions ---

@pytest.mark.asyncio
async def test_count_by_actions_matches_only_listed_actions(db):
    db.session.add_all(
        [
            _audit_log(id="a1", action="login", created_at=datetime(2026, 1, 5)),
            _audit_log(id="a2", action="logout", created_at=datetime(2026, 1, 5)),
            _audit_log(id="a3", action="agent_tool_invoke", created_at=datetime(2026, 1, 5)),
        ]
    )
    db.session.flush()

    count = await db.audit.count_by_actions(["login", "logout"])

    assert count == 2


@pytest.mark.asyncio
async def test_count_by_actions_respects_time_window(db):
    db.session.add_all(
        [
            _audit_log(id="a1", action="login", created_at=datetime(2026, 1, 1)),
            _audit_log(id="a2", action="login", created_at=datetime(2026, 6, 1)),
        ]
    )
    db.session.flush()

    count = await db.audit.count_by_actions(
        ["login"], start_at=datetime(2026, 3, 1), end_at=datetime(2026, 12, 1)
    )

    assert count == 1


@pytest.mark.asyncio
async def test_count_by_actions_empty_list_is_zero_without_querying(db):
    db.session.add(_audit_log(id="a1", action="login", created_at=datetime(2026, 1, 1)))
    db.session.flush()

    assert await db.audit.count_by_actions([]) == 0


# --- AuditRepository.count_by_action_prefix ---

@pytest.mark.asyncio
async def test_count_by_action_prefix_matches_prefix_only(db):
    db.session.add_all(
        [
            _audit_log(id="a1", action="admin.user.patch", created_at=datetime(2026, 1, 5)),
            _audit_log(id="a2", action="admin.team.delete", created_at=datetime(2026, 1, 5)),
            _audit_log(id="a3", action="llm_endpoint_create", created_at=datetime(2026, 1, 5)),
        ]
    )
    db.session.flush()

    count = await db.audit.count_by_action_prefix("admin.")

    assert count == 2


@pytest.mark.asyncio
async def test_count_by_action_prefix_does_not_match_substring_mid_string(db):
    # "team.admin.demote" contains "admin." but does not *start* with it --
    # must not be counted (proves LIKE 'prefix%' not '%prefix%').
    db.session.add(_audit_log(id="a1", action="team.admin.demote", created_at=datetime(2026, 1, 5)))
    db.session.flush()

    assert await db.audit.count_by_action_prefix("admin.") == 0


# --- AuditRepository.list_recent_chained ---

@pytest.mark.asyncio
async def test_list_recent_chained_orders_by_chain_seq_not_created_at(db):
    # created_at deliberately out of step with chain_seq: chain_seq=2's
    # created_at is *earlier* than chain_seq=1's. A created_at-based sort
    # would silently "fix" this; chain_seq-based ordering must not.
    db.session.add_all(
        [
            _audit_log(
                id="a1", action="x", chain_seq=1,
                created_at=datetime(2026, 1, 10),
            ),
            _audit_log(
                id="a2", action="x", chain_seq=2,
                created_at=datetime(2026, 1, 1),
            ),
            _audit_log(
                id="a3", action="x", chain_seq=3,
                created_at=datetime(2026, 1, 20),
            ),
        ]
    )
    db.session.flush()

    sample = await db.audit.list_recent_chained(limit=20)

    assert [log.chain_seq for log in sample] == [1, 2, 3]
    # created_at is carried through unsorted -- the anomaly is preserved for
    # the caller (ComplianceService) to detect, not hidden by this method.
    assert [log.created_at for log in sample] == [
        datetime(2026, 1, 10),
        datetime(2026, 1, 1),
        datetime(2026, 1, 20),
    ]


@pytest.mark.asyncio
async def test_list_recent_chained_respects_limit_and_takes_the_newest(db):
    for seq in range(1, 6):
        db.session.add(
            _audit_log(
                id=f"a{seq}", action="x", chain_seq=seq,
                created_at=datetime(2026, 1, seq),
            )
        )
    db.session.flush()

    sample = await db.audit.list_recent_chained(limit=2)

    # newest 2 by chain_seq (4, 5), returned ascending.
    assert [log.chain_seq for log in sample] == [4, 5]


@pytest.mark.asyncio
async def test_list_recent_chained_excludes_unchained_entries(db):
    db.session.add_all(
        [
            _audit_log(id="a1", action="x", chain_seq=None, created_at=datetime(2026, 1, 1)),
            _audit_log(id="a2", action="x", chain_seq=1, created_at=datetime(2026, 1, 2)),
        ]
    )
    db.session.flush()

    sample = await db.audit.list_recent_chained(limit=20)

    assert [log.id for log in sample] == ["a2"]


# --- UserRepository.count_by_role ---

@pytest.mark.asyncio
async def test_count_by_role_groups_correctly(db):
    db.session.add_all(
        [
            _user(id="u1", email="a@x.com", username="a", global_role="admin"),
            _user(id="u2", email="b@x.com", username="b", global_role="user"),
            _user(id="u3", email="c@x.com", username="c", global_role="user"),
            _user(id="u4", email="d@x.com", username="d", global_role="auditor"),
        ]
    )
    db.session.flush()

    dist = await db.user.count_by_role()

    assert dist == {"admin": 1, "user": 2, "auditor": 1}


@pytest.mark.asyncio
async def test_count_by_role_empty_table_returns_empty_dict(db):
    assert await db.user.count_by_role() == {}


# --- SessionRepository.count_created_between (fix 2: agent-mode only) ---

@pytest.mark.asyncio
async def test_count_created_between_excludes_ask_mode_sessions(db):
    db.session.add_all(
        [
            _session_row(id="s1", mode=SessionMode.AGENT.value, created_at=datetime(2026, 1, 5)),
            _session_row(id="s2", mode=SessionMode.ASK.value, created_at=datetime(2026, 1, 5)),
        ]
    )
    db.session.flush()

    count = await db.session_repo.count_created_between()

    assert count == 1


@pytest.mark.asyncio
async def test_count_created_between_respects_window(db):
    db.session.add_all(
        [
            _session_row(id="s1", mode=SessionMode.AGENT.value, created_at=datetime(2026, 1, 1)),
            _session_row(id="s2", mode=SessionMode.AGENT.value, created_at=datetime(2026, 6, 1)),
        ]
    )
    db.session.flush()

    count = await db.session_repo.count_created_between(
        start_at=datetime(2026, 3, 1), end_at=datetime(2026, 12, 1)
    )

    assert count == 1


# --- CheckpointRepository.count_created_between ---

@pytest.mark.asyncio
async def test_checkpoint_count_created_between_respects_window(db):
    db.session.add_all(
        [
            _checkpoint(id="c1", session_id="s1", created_at=datetime(2026, 1, 1)),
            _checkpoint(id="c2", session_id="s1", created_at=datetime(2026, 6, 1)),
        ]
    )
    db.session.flush()

    total = await db.checkpoint.count_created_between()
    windowed = await db.checkpoint.count_created_between(
        start_at=datetime(2026, 3, 1), end_at=datetime(2026, 12, 1)
    )

    assert total == 2
    assert windowed == 1


# --- LLMEndpointRepository.list_hosts ---

@pytest.mark.asyncio
async def test_list_hosts_extracts_hostname_without_scheme_or_path(db):
    db.session.add_all(
        [
            _llm_endpoint(id="e1", base_url="https://api.openai.com/v1"),
            _llm_endpoint(id="e2", base_url="http://llm.internal.corp:8080/v1"),
            _llm_endpoint(id="e3", base_url="llm.no-scheme.example.com/v1"),
        ]
    )
    db.session.flush()

    hosts = await db.llm_endpoint.list_hosts()

    assert sorted(hosts) == [
        "api.openai.com",
        "llm.internal.corp",
        "llm.no-scheme.example.com",
    ]


@pytest.mark.asyncio
async def test_list_hosts_skips_empty_base_url(db):
    db.session.add(_llm_endpoint(id="e1", base_url=""))
    db.session.flush()

    assert await db.llm_endpoint.list_hosts() == []
