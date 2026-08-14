#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ToolGatePolicy: normalize_domain / effective_gate_profile /
gate_profile_settings / tool_gate_call_level_enabled / should_audit /
compute_gated_flag / authorizes_tool / requires_policy_approval.

None of these 8 public members had direct tests before this file (only
exercised indirectly through BaseAgent/ToolBatchExecutor integration tests).
`get_runtime_config` is monkeypatched per-test via the module's imported
name (mirrors test_task_runner_factory.py's
`patch("...task_runner_factory.get_runtime_config", ...)` pattern) rather
than relying on the app-wide warm cache, so risk-list/gate-profile/
feature-flag values are explicit and independent of container warmup
order. Default `AppConfig()` values mirror config.yaml:230-266 exactly
(loose/standard/strict gate_profiles, tool_gate_risk_list, critical_action_patterns).
"""
from typing import Any, Dict, Optional

import pytest

from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from app.domain.models.app_config import AppConfig
from app.domain.models.session import Session
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.agents.tool_batch_executor import PreparedToolCall
from app.domain.services.agents.tool_gate_policy import ToolGatePolicy


NEVER_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.NEVER,
)
ALWAYS_POLICY = NEVER_POLICY.model_copy(update={"approval": ApprovalMode.ALWAYS})
POLICY_GATED = NEVER_POLICY.model_copy(update={"approval": ApprovalMode.POLICY})


def _runtime_settings(**overrides) -> AgentRuntimeSettings:
    return AgentRuntimeSettings(**overrides)


class _FakeSessionRepo:
    def __init__(self, session: Optional[Session]) -> None:
        self._session = session

    async def get_by_id(self, session_id: str):
        return self._session


class _FakeUow:
    def __init__(self, session_repo: _FakeSessionRepo) -> None:
        self.session = session_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _policy(
        *,
        gate_profile: Optional[str],
        session: Optional[Session] = None,
        uow_factory=None,
        allowed_tool_names=None,
) -> ToolGatePolicy:
    factory = uow_factory or (lambda: _FakeUow(_FakeSessionRepo(session)))
    return ToolGatePolicy(
        runtime_settings=_runtime_settings(gate_profile=gate_profile),
        session_id="session-1",
        uow_factory=factory,
        allowed_tool_names=allowed_tool_names,
    )


def _call(function_name: str, args: Optional[Dict[str, Any]] = None, *, policy=POLICY_GATED) -> PreparedToolCall:
    return PreparedToolCall(
        tool_call_id="call-1",
        tool=None,  # unused by authorizes_tool/requires_policy_approval
        function_name=function_name,
        normalized_args=args or {},
        policy=policy,
        requires_approval=False,
        ordinal=0,
    )


@pytest.fixture
def default_runtime_config(monkeypatch):
    """Deterministic AppConfig matching config.yaml:230-266's defaults."""
    config = AppConfig()
    monkeypatch.setattr(
        "app.domain.services.agents.tool_gate_policy.get_runtime_config",
        lambda: config,
    )
    return config


# ---------------------------------------------------------------------------
# normalize_domain
# ---------------------------------------------------------------------------

def test_normalize_domain_with_scheme_lowercases_host_and_drops_port():
    assert ToolGatePolicy.normalize_domain("https://Example.com:8080/path") == "example.com"


def test_normalize_domain_without_scheme_defaults_to_https():
    assert ToolGatePolicy.normalize_domain("example.com/page") == "example.com"


def test_normalize_domain_strips_userinfo_and_port():
    assert ToolGatePolicy.normalize_domain("http://user:pass@sub.example.com:9999") == "sub.example.com"


def test_normalize_domain_empty_string_returns_empty():
    assert ToolGatePolicy.normalize_domain("") == ""


def test_normalize_domain_malformed_url_returns_empty():
    assert ToolGatePolicy.normalize_domain("://not a url") == ""


# ---------------------------------------------------------------------------
# effective_gate_profile
# ---------------------------------------------------------------------------

def test_effective_gate_profile_defaults_to_standard_when_unset():
    policy = _policy(gate_profile=None)
    assert policy.effective_gate_profile() == "standard"


def test_effective_gate_profile_lowercases_configured_value():
    policy = _policy(gate_profile="STRICT")
    assert policy.effective_gate_profile() == "strict"


# ---------------------------------------------------------------------------
# gate_profile_settings
# ---------------------------------------------------------------------------

def test_gate_profile_settings_resolves_strict_profile(default_runtime_config):
    policy = _policy(gate_profile="strict")

    settings = policy.gate_profile_settings()

    assert settings.tool_gate_call_level_enabled is True
    assert settings.selective_critical_only is False


def test_gate_profile_settings_resolves_loose_profile(default_runtime_config):
    policy = _policy(gate_profile="loose")

    settings = policy.gate_profile_settings()

    assert settings.tool_gate_call_level_enabled is False
    assert settings.selective_critical_only is False


def test_gate_profile_settings_unknown_profile_falls_back_to_standard(default_runtime_config):
    policy = _policy(gate_profile="not-a-real-profile")

    settings = policy.gate_profile_settings()

    assert settings.selective_critical_only is True  # standard's value


# ---------------------------------------------------------------------------
# tool_gate_call_level_enabled
# ---------------------------------------------------------------------------

def test_tool_gate_call_level_enabled_false_when_feature_flag_disabled(monkeypatch):
    config = AppConfig()
    config.feature_flags.enable_hitl_gates = False
    monkeypatch.setattr(
        "app.domain.services.agents.tool_gate_policy.get_runtime_config",
        lambda: config,
    )
    policy = _policy(gate_profile="strict")

    assert policy.tool_gate_call_level_enabled() is False


def test_tool_gate_call_level_enabled_uses_explicit_override(default_runtime_config):
    policy = ToolGatePolicy(
        runtime_settings=AgentRuntimeSettings(gate_profile="loose", tool_gate_call_level_enabled=True),
        session_id="session-1",
        uow_factory=lambda: _FakeUow(_FakeSessionRepo(None)),
    )

    # loose profile itself says False, but the explicit override wins.
    assert policy.tool_gate_call_level_enabled() is True


def test_tool_gate_call_level_enabled_uses_profile_when_no_override(default_runtime_config):
    policy = _policy(gate_profile="standard")

    assert policy.tool_gate_call_level_enabled() is True


def test_tool_gate_call_level_enabled_uses_global_default_when_no_profile(default_runtime_config):
    policy = _policy(gate_profile=None)

    # No gate_profile at all -> falls through to runtime.hitl.tool_gate_call_level_enabled (False by default).
    assert policy.tool_gate_call_level_enabled() is False


# ---------------------------------------------------------------------------
# should_audit
# ---------------------------------------------------------------------------

def test_should_audit_false_without_gate_profile():
    policy = _policy(gate_profile=None)
    assert policy.should_audit("browser_click") is False


@pytest.mark.parametrize("tool_name", ["browser_click", "BROWSER_NAVIGATE", "browser_console_exec"])
def test_should_audit_true_for_browser_prefixed_tools(tool_name):
    policy = _policy(gate_profile="strict")
    assert policy.should_audit(tool_name) is True


@pytest.mark.parametrize("tool_name", ["shell_execute", "SHELL_EXECUTE", "a2a", "A2A"])
def test_should_audit_true_for_shell_and_a2a(tool_name):
    policy = _policy(gate_profile="strict")
    assert policy.should_audit(tool_name) is True


@pytest.mark.parametrize("tool_name", ["mcp_jira_create_issue", "mcp"])
def test_should_audit_true_for_mcp_tools(tool_name):
    policy = _policy(gate_profile="strict")
    assert policy.should_audit(tool_name) is True


def test_should_audit_false_for_unrelated_tool():
    policy = _policy(gate_profile="strict")
    assert policy.should_audit("read_file") is False


def test_should_audit_false_for_write_file_despite_being_risk_listed():
    """Known Phase A ledger gap (do not change behavior here): should_audit's
    prefix/name set does not include write_file/replace_in_file, even though
    they are in hitl.tool_gate_risk_list and can make compute_gated_flag
    return True. Because ToolAuditRecorder.maybe_record_tool_audit early-exits
    on `not should_audit(...)`, a gated write_file call is never audited and
    record_gate_hit is never called for it — narrower than compute_gated_flag.
    This test asserts the current (as-is) behavior, not the desired one."""
    policy = _policy(gate_profile="strict")
    assert policy.should_audit("write_file") is False


# ---------------------------------------------------------------------------
# compute_gated_flag
# ---------------------------------------------------------------------------

def test_compute_gated_flag_false_without_gate_profile(default_runtime_config):
    policy = _policy(gate_profile=None)
    assert policy.compute_gated_flag("write_file", {}) is False


def test_compute_gated_flag_false_when_tool_not_in_risk_list(default_runtime_config):
    policy = _policy(gate_profile="strict")
    assert policy.compute_gated_flag("read_file", {}) is False


def test_compute_gated_flag_true_for_risk_listed_tool_under_strict_profile(default_runtime_config):
    """strict profile: selective_critical_only=False -> falls through to
    tool_gate_call_level_enabled(), which is True for strict."""
    policy = _policy(gate_profile="strict")
    assert policy.compute_gated_flag("write_file", {}) is True


def test_compute_gated_flag_false_for_risk_listed_tool_under_loose_profile(default_runtime_config):
    """loose profile: selective_critical_only=False, tool_gate_call_level_enabled=False."""
    policy = _policy(gate_profile="loose")
    assert policy.compute_gated_flag("write_file", {}) is False


def test_compute_gated_flag_standard_profile_requires_critical_action_match(default_runtime_config):
    """standard profile: selective_critical_only=True -> only critical-looking
    arguments gate the call."""
    policy = _policy(gate_profile="standard")

    assert policy.compute_gated_flag("browser_click", {"selector": "#save-button"}) is False
    assert policy.compute_gated_flag("browser_click", {"selector": "#btn-confirm-delete"}) is True


# ---------------------------------------------------------------------------
# authorizes_tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_authorizes_tool_true_when_no_allowlist_configured():
    policy = _policy(gate_profile=None, allowed_tool_names=None)
    assert await policy.authorizes_tool(_call("write_file")) is True


@pytest.mark.asyncio
async def test_authorizes_tool_true_for_wildcard_match():
    policy = _policy(gate_profile=None, allowed_tool_names=["browser_*"])
    assert await policy.authorizes_tool(_call("browser_click")) is True


@pytest.mark.asyncio
async def test_authorizes_tool_false_when_not_in_allowlist():
    policy = _policy(gate_profile=None, allowed_tool_names=["shell_execute"])
    assert await policy.authorizes_tool(_call("write_file")) is False


# ---------------------------------------------------------------------------
# requires_policy_approval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_requires_policy_approval_always_mode_is_true_without_touching_uow():
    def _boom_uow_factory():
        raise AssertionError("uow must not be opened for ApprovalMode.ALWAYS")

    policy = _policy(gate_profile=None, uow_factory=_boom_uow_factory)

    assert await policy.requires_policy_approval(_call("write_file", policy=ALWAYS_POLICY)) is True


@pytest.mark.asyncio
async def test_requires_policy_approval_never_mode_is_false_without_touching_uow():
    def _boom_uow_factory():
        raise AssertionError("uow must not be opened for ApprovalMode.NEVER")

    policy = _policy(gate_profile=None, uow_factory=_boom_uow_factory)

    assert await policy.requires_policy_approval(_call("write_file", policy=NEVER_POLICY)) is False


@pytest.mark.asyncio
async def test_requires_policy_approval_false_when_session_has_no_operator_scope(default_runtime_config):
    session = Session(id="session-1", title="s", operator_scope=None)
    policy = _policy(gate_profile="strict", session=session)

    assert await policy.requires_policy_approval(_call("write_file")) is False


@pytest.mark.asyncio
async def test_requires_policy_approval_false_when_session_missing(default_runtime_config):
    policy = _policy(gate_profile="strict", session=None)

    assert await policy.requires_policy_approval(_call("write_file")) is False


@pytest.mark.asyncio
async def test_requires_policy_approval_true_for_risk_gated_tool_under_strict_profile(default_runtime_config):
    session = Session(id="session-1", title="s", operator_scope="owned")
    policy = _policy(gate_profile="strict", session=session)

    assert await policy.requires_policy_approval(_call("write_file")) is True


@pytest.mark.asyncio
async def test_requires_policy_approval_false_when_tool_already_approved(default_runtime_config):
    session = Session(
        id="session-1",
        title="s",
        operator_scope="owned",
        pending_metadata={"approved_tools": ["write_file"]},
    )
    policy = _policy(gate_profile="strict", session=session)

    assert await policy.requires_policy_approval(_call("write_file")) is False


@pytest.mark.asyncio
async def test_requires_policy_approval_true_for_first_visit_to_new_domain(default_runtime_config):
    session = Session(id="session-1", title="s", operator_scope="owned")
    policy = _policy(gate_profile="loose", session=session)

    call = _call("browser_navigate", {"url": "https://new-site.example.com"})

    assert await policy.requires_policy_approval(call) is True


@pytest.mark.asyncio
async def test_requires_policy_approval_false_when_domain_already_whitelisted(default_runtime_config):
    session = Session(id="session-1", title="s", operator_scope="owned")
    policy = ToolGatePolicy(
        runtime_settings=AgentRuntimeSettings(gate_profile="loose", operator_domains=["example.com"]),
        session_id="session-1",
        uow_factory=lambda: _FakeUow(_FakeSessionRepo(session)),
    )

    call = _call("browser_navigate", {"url": "https://example.com/page"})

    # browser_navigate is not itself risk-listed, and the domain is
    # pre-whitelisted, so neither the domain gate nor the risk gate fires.
    assert await policy.requires_policy_approval(call) is False


@pytest.mark.asyncio
async def test_requires_policy_approval_false_when_domain_already_visited(default_runtime_config):
    session = Session(
        id="session-1",
        title="s",
        operator_scope="owned",
        pending_metadata={"visited_domains": ["new-site.example.com"]},
    )
    policy = _policy(gate_profile="loose", session=session)

    call = _call("browser_navigate", {"url": "https://new-site.example.com"})

    assert await policy.requires_policy_approval(call) is False


@pytest.mark.asyncio
async def test_requires_policy_approval_standard_profile_needs_critical_action_match(default_runtime_config):
    """standard profile: selective_critical_only=True narrows the risk gate
    to only critical-looking arguments, mirroring compute_gated_flag."""
    session = Session(id="session-1", title="s", operator_scope="owned")
    policy = _policy(gate_profile="standard", session=session)

    benign_call = _call("browser_click", {"selector": "#save-button"})
    assert await policy.requires_policy_approval(benign_call) is False

    critical_call = _call("browser_click", {"selector": "#btn-confirm-delete"})
    assert await policy.requires_policy_approval(critical_call) is True
