"""Retention purge for soft-deleted rows must run, batch, and stay auditable.

Historically the 30-day recycle-bin retention existed only as a TODO comment:
the scheduler leader tick had no purge step, so soft-deleted sessions and
knowledge bases lived in the database forever — at odds with the compliance
domain's storage-limitation stance.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.application.services.recycle_bin_retention_service import (
    RecycleBinRetentionService,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _uow(session_ids=(), kb_ids=()):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.session.list_deleted_before = AsyncMock(return_value=list(session_ids))
    uow.session.purge = AsyncMock(return_value=True)
    uow.knowledge_base.list_deleted_kbs_before = AsyncMock(return_value=list(kb_ids))
    uow.knowledge_base.purge_kb = AsyncMock(return_value=True)
    return uow


def test_disabled_when_retention_days_is_zero() -> None:
    uow = _uow()
    service = RecycleBinRetentionService(lambda: uow, retention_days=0)

    assert not service.enabled
    result = asyncio.run(service.purge_expired(now=NOW))

    assert result == {"sessions": 0, "knowledge_bases": 0}
    uow.session.list_deleted_before.assert_not_awaited()


def test_purges_expired_rows_and_audits_each() -> None:
    uow = _uow(session_ids=["s1", "s2"], kb_ids=["kb1"])
    audit = MagicMock()
    audit.record = AsyncMock()
    service = RecycleBinRetentionService(
        lambda: uow,
        retention_days=30,
        batch_size=50,
        audit_service=audit,
    )

    result = asyncio.run(service.purge_expired(now=NOW))

    assert result == {"sessions": 2, "knowledge_bases": 1}
    cutoff = NOW - timedelta(days=30)
    uow.session.list_deleted_before.assert_awaited_once_with(cutoff, limit=50)
    uow.knowledge_base.list_deleted_kbs_before.assert_awaited_once_with(cutoff, limit=50)
    uow.session.purge.assert_any_await("s1")
    uow.knowledge_base.purge_kb.assert_awaited_once_with("kb1")
    uow.commit.assert_awaited_once()
    audited = [call.args[0] for call in audit.record.await_args_list]
    assert {(log.resource_type, log.resource_id) for log in audited} == {
        ("session", "s1"),
        ("session", "s2"),
        ("knowledge_base", "kb1"),
    }
    assert all(log.action == "recycle_bin.auto_purge" for log in audited)


def test_missed_purge_rows_are_not_counted_or_audited() -> None:
    uow = _uow(session_ids=["s1"])
    uow.session.purge = AsyncMock(return_value=False)
    audit = MagicMock()
    audit.record = AsyncMock()
    service = RecycleBinRetentionService(
        lambda: uow,
        retention_days=30,
        audit_service=audit,
    )

    result = asyncio.run(service.purge_expired(now=NOW))

    assert result == {"sessions": 0, "knowledge_bases": 0}
    audit.record.assert_not_awaited()
