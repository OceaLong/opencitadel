#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone

import pytest

from app.domain.models.audit_log import AuditLog
from app.domain.services.audit_chain import (
    GENESIS,
    compute_entry_hash,
    entry_fields,
)


def test_compute_entry_hash_deterministic():
    secret = "test-secret-key-at-least-32-chars!!"
    fields = entry_fields(
        chain_seq=1,
        id="log-1",
        actor_user_id="u1",
        actor_ip="127.0.0.1",
        action="agent_tool_invoke",
        resource_type="session",
        resource_id="s1",
        team_id=None,
        request_id="req-1",
        metadata={"tool": "browser_click"},
        created_at=datetime(2026, 7, 3, 0, 0, 0, tzinfo=timezone.utc),
    )
    h1 = compute_entry_hash(secret, fields, GENESIS)
    h2 = compute_entry_hash(secret, fields, GENESIS)
    assert h1 == h2
    assert len(h1) == 64


@pytest.mark.asyncio
async def test_verify_chain_detects_tamper():
    from app.application.services.audit_service import AuditService

    secret = "test-secret-key-at-least-32-chars!!"

    class _Repo:
        async def list_chained(self, **kwargs):
            created = datetime(2026, 7, 3, 0, 0, 0, tzinfo=timezone.utc)
            f1 = entry_fields(
                chain_seq=1,
                id="a",
                actor_user_id=None,
                actor_ip="",
                action="test",
                resource_type="",
                resource_id="",
                team_id=None,
                request_id="",
                metadata={},
                created_at=created,
            )
            h1 = compute_entry_hash(secret, f1, GENESIS)
            log1 = AuditLog(
                id="a",
                action="test",
                chain_seq=1,
                prev_hash=GENESIS,
                entry_hash=h1,
                created_at=created,
            )
            log2 = AuditLog(
                id="b",
                action="test",
                chain_seq=2,
                prev_hash=h1,
                entry_hash="bad" * 16,
                created_at=created,
            )
            return [log1, log2]

    class _Uow:
        def __init__(self):
            self.audit = _Repo()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    service = AuditService(lambda: _Uow())

    import app.application.services.audit_service as audit_mod

    original = audit_mod.get_settings

    class _Settings:
        api_key_secret = secret

    audit_mod.get_settings = lambda: _Settings()  # type: ignore[assignment]
    try:
        result = await service.verify_chain()
    finally:
        audit_mod.get_settings = original

    assert result["ok"] is False
    assert result["first_broken_seq"] == 2
