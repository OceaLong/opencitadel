#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ToolAuditRecorder: gate-hit metric must fire exactly when a call is
recorded with gated=True, tagged with the session's gate_profile.

Task 6 (Phase C engineering-debt cleanup) appends coverage for the two
remaining gaps: `maybe_record_tool_audit` (the should_audit-gated wrapper
around record_tool_audit) and `record_visited_domain` (the browser
first-visit tracking write).
"""
import asyncio
import time
from types import SimpleNamespace
from typing import List

from prometheus_client import REGISTRY

from app.domain.models.app_config import AppConfig
from app.domain.models.audit_log import AuditLog
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.tool_audit_recorder import ToolAuditRecorder
from app.domain.services.agents.tool_gate_policy import ToolGatePolicy
from tests.app.domain.services.agents.conftest import agent_test_runtime_settings


class _FakeAuditRepo:
    def __init__(self) -> None:
        self.items: List[AuditLog] = []

    async def add(self, log: AuditLog) -> None:
        self.items.append(log)


class _FakeSessionRepo:
    async def get_by_id(self, session_id: str):
        return type("Session", (), {"owner_user_id": "u1", "team_id": None})()


class _FakeUow:
    def __init__(self, audit_repo: _FakeAuditRepo) -> None:
        self.audit = audit_repo
        self.session = _FakeSessionRepo()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


def _counter_value(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _make_recorder(audit_repo: _FakeAuditRepo, *, gate_profile: str) -> ToolAuditRecorder:
    runtime_settings = agent_test_runtime_settings(gate_profile=gate_profile)
    uow_factory = lambda: _FakeUow(audit_repo)  # noqa: E731
    gate_policy = ToolGatePolicy(
        runtime_settings=runtime_settings,
        session_id="session-1",
        uow_factory=uow_factory,
    )
    return ToolAuditRecorder(
        uow_factory=uow_factory,
        session_id="session-1",
        runtime_settings=runtime_settings,
        gate_policy=gate_policy,
    )


def test_record_tool_audit_with_gated_true_records_gate_hit():
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder(audit_repo, gate_profile="strict")
    before = _counter_value("governance_gate_hits_total", {"gate": "strict"})

    asyncio.run(
        recorder.record_tool_audit(
            tool_name="browser_click",
            arguments={},
            result=ToolResult(success=True, message="ok"),
            duration_ms=10,
            gated=True,
        )
    )

    after = _counter_value("governance_gate_hits_total", {"gate": "strict"})
    assert len(audit_repo.items) == 1
    assert audit_repo.items[0].metadata["gated"] is True
    assert after - before == 1.0


def test_record_tool_audit_with_gated_false_does_not_record_gate_hit():
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder(audit_repo, gate_profile="standard")
    before = _counter_value("governance_gate_hits_total", {"gate": "standard"})

    asyncio.run(
        recorder.record_tool_audit(
            tool_name="browser_click",
            arguments={},
            result=ToolResult(success=True, message="ok"),
            duration_ms=10,
            gated=False,
        )
    )

    after = _counter_value("governance_gate_hits_total", {"gate": "standard"})
    assert len(audit_repo.items) == 1
    assert audit_repo.items[0].metadata["gated"] is False
    assert after - before == 0.0


def test_record_tool_denied_writes_audit_and_metric():
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder(audit_repo, gate_profile="strict")
    before = _counter_value(
        "governance_policy_denials_total",
        {"layer": "execution", "tool": "write_file"},
    )

    asyncio.run(
        recorder.record_tool_denied(
            tool_name="write_file",
            layer="execution",
            reason="当前会话策略禁止工具[write_file]",
        )
    )

    after = _counter_value(
        "governance_policy_denials_total",
        {"layer": "execution", "tool": "write_file"},
    )
    assert after - before == 1.0
    assert len(audit_repo.items) == 1
    entry = audit_repo.items[0]
    assert entry.action == "agent_tool_denied"
    assert entry.resource_id == "session-1"
    assert entry.metadata["tool"] == "write_file"
    assert entry.metadata["layer"] == "execution"
    assert entry.metadata["reason"] == "当前会话策略禁止工具[write_file]"
    assert entry.metadata["gate_profile"] == "strict"


def test_record_tool_denied_normalizes_multi_tool_label_but_keeps_audit_detail():
    """Task 2 fix round 1 (#1): the `exposure`-layer raise site in
    capability_policy.py's for_child joins multiple denied tool names with
    commas into one string (see test_capability_policy.py's
    test_agent_child_policy_cannot_expand_beyond_allowlist). That string
    must never reach record_policy_denial as a Prometheus label — unbounded
    cardinality — so it gets normalized to "multiple" for the metric while
    the audit metadata keeps the original comma-joined detail intact."""
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder(audit_repo, gate_profile="strict")
    multi_tool_name = "read_secret, shell_execute, write_file"
    before_multiple = _counter_value(
        "governance_policy_denials_total",
        {"layer": "exposure", "tool": "multiple"},
    )
    before_raw = _counter_value(
        "governance_policy_denials_total",
        {"layer": "exposure", "tool": multi_tool_name},
    )

    asyncio.run(
        recorder.record_tool_denied(
            tool_name=multi_tool_name,
            layer="exposure",
            reason="子 Agent 工具请求超出父策略: read_secret, shell_execute, write_file",
        )
    )

    after_multiple = _counter_value(
        "governance_policy_denials_total",
        {"layer": "exposure", "tool": "multiple"},
    )
    after_raw = _counter_value(
        "governance_policy_denials_total",
        {"layer": "exposure", "tool": multi_tool_name},
    )
    assert after_multiple - before_multiple == 1.0
    # The raw comma-joined string must never itself appear as a label value.
    assert after_raw - before_raw == 0.0
    # Audit detail is unaffected: the full tool list stays in metadata.
    assert audit_repo.items[0].metadata["tool"] == multi_tool_name


def test_record_tool_denied_single_tool_name_is_not_normalized():
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder(audit_repo, gate_profile="strict")
    before = _counter_value(
        "governance_policy_denials_total",
        {"layer": "execution", "tool": "write_file"},
    )

    asyncio.run(
        recorder.record_tool_denied(
            tool_name="write_file",
            layer="execution",
            reason="当前会话策略禁止工具[write_file]",
        )
    )

    after = _counter_value(
        "governance_policy_denials_total",
        {"layer": "execution", "tool": "write_file"},
    )
    assert after - before == 1.0
    assert audit_repo.items[0].metadata["tool"] == "write_file"


def test_record_tool_denied_truncates_and_redacts_reason():
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder(audit_repo, gate_profile="strict")

    long_reason = f"denied token=super-secret-value user@example.com {'x' * 600}"
    asyncio.run(
        recorder.record_tool_denied(
            tool_name="write_file",
            layer="assembly",
            reason=long_reason,
        )
    )

    reason = audit_repo.items[0].metadata["reason"]
    assert len(reason) <= 500
    assert "super-secret-value" not in reason
    assert "user@example.com" not in reason


def test_record_tool_denied_swallows_write_failure():
    class _BoomUow:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *args):
            return False

    runtime_settings = agent_test_runtime_settings(gate_profile="strict")
    gate_policy = ToolGatePolicy(
        runtime_settings=runtime_settings,
        session_id="session-1",
        uow_factory=lambda: _BoomUow(),
    )
    recorder = ToolAuditRecorder(
        uow_factory=lambda: _BoomUow(),
        session_id="session-1",
        runtime_settings=runtime_settings,
        gate_policy=gate_policy,
    )

    # Must not raise: audit-recording failures never block the tool-denial
    # path that triggered them (mirrors maybe_record_tool_audit).
    asyncio.run(
        recorder.record_tool_denied(
            tool_name="write_file",
            layer="execution",
            reason="denied",
        )
    )


# ---------------------------------------------------------------------------
# maybe_record_tool_audit
# ---------------------------------------------------------------------------

def _make_recorder_with_runtime_config(audit_repo: _FakeAuditRepo, *, gate_profile: str, monkeypatch) -> ToolAuditRecorder:
    """Like `_make_recorder`, but pins ToolGatePolicy's `get_runtime_config`
    to a known `AppConfig()` (mirrors config.yaml:230-266's defaults) so
    `compute_gated_flag`'s risk-list lookup is deterministic regardless of
    whether the app-wide config cache happens to be warm in this test run."""
    config = AppConfig()
    monkeypatch.setattr(
        "app.domain.services.agents.tool_gate_policy.get_runtime_config",
        lambda: config,
    )
    return _make_recorder(audit_repo, gate_profile=gate_profile)


def test_maybe_record_tool_audit_skips_write_when_should_audit_is_false(monkeypatch):
    """`read_file` matches none of should_audit's prefixes (browser_/shell_execute/a2a/mcp_),
    so the early-return must skip writing an audit row entirely — even
    though a gate_profile is configured."""
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder_with_runtime_config(audit_repo, gate_profile="strict", monkeypatch=monkeypatch)

    asyncio.run(
        recorder.maybe_record_tool_audit(
            tool_name="read_file",
            arguments={},
            result=ToolResult(success=True, message="ok"),
            started=time.monotonic(),
        )
    )

    assert audit_repo.items == []


def test_maybe_record_tool_audit_normal_path_computes_duration_and_gated_flag(monkeypatch):
    """browser_click passes should_audit; under the strict profile it is
    also risk-listed and call-level-gated, so compute_gated_flag should be
    True and get threaded through to the written audit row."""
    audit_repo = _FakeAuditRepo()
    recorder = _make_recorder_with_runtime_config(audit_repo, gate_profile="strict", monkeypatch=monkeypatch)
    started = time.monotonic() - 0.05

    asyncio.run(
        recorder.maybe_record_tool_audit(
            tool_name="browser_click",
            arguments={"selector": "#save-button"},
            result=ToolResult(success=True, message="ok"),
            started=started,
        )
    )

    assert len(audit_repo.items) == 1
    entry = audit_repo.items[0]
    assert entry.metadata["tool"] == "browser_click"
    assert entry.metadata["gated"] is True
    assert entry.metadata["duration_ms"] >= 40


def test_maybe_record_tool_audit_swallows_write_failure():
    """Mirrors record_tool_denied_swallows_write_failure: a failure while
    writing the audit row must never propagate out of the tool-call path
    that triggered it."""

    class _BoomUow:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *args):
            return False

    runtime_settings = agent_test_runtime_settings(gate_profile="strict")
    gate_policy = ToolGatePolicy(
        runtime_settings=runtime_settings,
        session_id="session-1",
        uow_factory=lambda: _BoomUow(),
    )
    recorder = ToolAuditRecorder(
        uow_factory=lambda: _BoomUow(),
        session_id="session-1",
        runtime_settings=runtime_settings,
        gate_policy=gate_policy,
    )

    # Must not raise, even though should_audit("browser_click") is True and
    # record_tool_audit's uow immediately blows up.
    asyncio.run(
        recorder.maybe_record_tool_audit(
            tool_name="browser_click",
            arguments={},
            result=ToolResult(success=True, message="ok"),
            started=time.monotonic(),
        )
    )


# ---------------------------------------------------------------------------
# record_visited_domain
# ---------------------------------------------------------------------------

class _FakeSessionRepoWithMetadata:
    def __init__(self, *, pending_metadata=None, exists: bool = True) -> None:
        self.pending_metadata = pending_metadata
        self.exists = exists
        self.set_calls: List[tuple] = []

    async def get_by_id(self, session_id: str):
        if not self.exists:
            return None
        return SimpleNamespace(pending_metadata=self.pending_metadata)

    async def set_pending_metadata(self, session_id: str, metadata) -> None:
        self.set_calls.append((session_id, metadata))
        self.pending_metadata = metadata


class _FakeSessionOnlyUow:
    def __init__(self, session_repo: _FakeSessionRepoWithMetadata) -> None:
        self.session = session_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _make_recorder_for_domain(session_repo: _FakeSessionRepoWithMetadata) -> ToolAuditRecorder:
    runtime_settings = agent_test_runtime_settings(gate_profile="loose")
    uow_factory = lambda: _FakeSessionOnlyUow(session_repo)  # noqa: E731
    gate_policy = ToolGatePolicy(
        runtime_settings=runtime_settings,
        session_id="session-1",
        uow_factory=uow_factory,
    )
    return ToolAuditRecorder(
        uow_factory=uow_factory,
        session_id="session-1",
        runtime_settings=runtime_settings,
        gate_policy=gate_policy,
    )


def test_record_visited_domain_appends_new_domain():
    session_repo = _FakeSessionRepoWithMetadata(pending_metadata={})
    recorder = _make_recorder_for_domain(session_repo)

    asyncio.run(recorder.record_visited_domain({"url": "https://Example.com/page"}))

    assert len(session_repo.set_calls) == 1
    _, metadata = session_repo.set_calls[0]
    assert metadata["visited_domains"] == ["example.com"]


def test_record_visited_domain_does_not_duplicate_existing_domain():
    session_repo = _FakeSessionRepoWithMetadata(pending_metadata={"visited_domains": ["example.com"]})
    recorder = _make_recorder_for_domain(session_repo)

    asyncio.run(recorder.record_visited_domain({"url": "https://example.com/another-page"}))

    assert session_repo.set_calls == []


def test_record_visited_domain_no_url_is_a_noop():
    session_repo = _FakeSessionRepoWithMetadata(pending_metadata={})
    recorder = _make_recorder_for_domain(session_repo)

    asyncio.run(recorder.record_visited_domain({}))

    assert session_repo.set_calls == []


def test_record_visited_domain_missing_session_is_a_noop():
    session_repo = _FakeSessionRepoWithMetadata(exists=False)
    recorder = _make_recorder_for_domain(session_repo)

    asyncio.run(recorder.record_visited_domain({"url": "https://example.com"}))

    assert session_repo.set_calls == []
