#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
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
async def test_verify_chain_detects_tamper_and_emits_critical_alert(caplog):
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
        audit_signing_key = secret
        audit_signing_key_id = "primary"
        audit_previous_signing_keys = {}
        api_key_secret = "legacy-api-key"
        api_key_previous_secrets = {}

    audit_mod.get_settings = lambda: _Settings()  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.CRITICAL):
            result = await service.verify_chain()
    finally:
        audit_mod.get_settings = original

    assert result["ok"] is False
    assert result["first_broken_seq"] == 2
    assert "AUDIT_CHAIN_INTEGRITY_FAILURE" in caplog.text


@pytest.mark.asyncio
async def test_session_verification_uses_global_chain_not_invalid_filtered_subset(
    monkeypatch,
):
    from app.application.services.audit_service import AuditService

    secret = "test-secret-key-at-least-32-chars!!"
    created = datetime(2026, 7, 3, 0, 0, 0, tzinfo=timezone.utc)
    logs = []
    previous = GENESIS
    for seq, resource_id in ((1, "other"), (2, "session-1"), (3, "other")):
        fields = entry_fields(
            chain_seq=seq,
            id=f"log-{seq}",
            actor_user_id=None,
            actor_ip="",
            action="test",
            resource_type="session",
            resource_id=resource_id,
            team_id=None,
            request_id="",
            metadata={},
            created_at=created,
        )
        entry_hash = compute_entry_hash(secret, fields, previous)
        logs.append(
            AuditLog(
                id=f"log-{seq}",
                action="test",
                resource_type="session",
                resource_id=resource_id,
                chain_seq=seq,
                signing_key_id="primary",
                prev_hash=previous,
                entry_hash=entry_hash,
                created_at=created,
            )
        )
        previous = entry_hash

    class _Repo:
        async def list_chained(self, *, resource_id=None, **kwargs):
            if resource_id:
                return [log for log in logs if log.resource_id == resource_id]
            return logs

    class _Uow:
        audit = _Repo()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Settings:
        audit_signing_key = secret
        audit_signing_key_id = "primary"
        audit_previous_signing_keys = {}
        api_key_secret = "legacy-api-key"
        api_key_previous_secrets = {}

    monkeypatch.setattr(
        "app.application.services.audit_service.get_settings",
        lambda: _Settings(),
    )

    result = await AuditService(lambda: _Uow()).verify_session_chain("session-1")

    assert result["ok"] is True
    assert result["session_ok"] is True
    assert result["session_entries"] == 1
