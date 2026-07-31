#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resolve the flow and capability policy for a session's selected mode/resources."""
from dataclasses import dataclass
from enum import Enum

from app.domain.models.codebase import SessionMode
from app.domain.services.tools.capability_policy import CapabilityPolicy


class FlowKind(str, Enum):
    DOC_ASK = "doc_ask"
    CODE_ASK = "code_ask"
    HYBRID_ASK = "hybrid_ask"
    PLANNER_REACT = "planner_react"


@dataclass(frozen=True)
class SessionFlowDecision:
    flow_kind: FlowKind
    policy: CapabilityPolicy


class SessionFlowResolver:
    """The single routing matrix for session modes and bound resources."""

    @staticmethod
    def resolve(
            mode: SessionMode,
            has_kb: bool,
            has_codebase: bool,
    ) -> SessionFlowDecision:
        policy = CapabilityPolicy.for_mode(mode)
        if mode == SessionMode.AGENT:
            return SessionFlowDecision(FlowKind.PLANNER_REACT, policy)
        if has_kb and has_codebase:
            return SessionFlowDecision(FlowKind.HYBRID_ASK, policy)
        if has_codebase:
            return SessionFlowDecision(FlowKind.CODE_ASK, policy)
        if has_kb:
            return SessionFlowDecision(FlowKind.DOC_ASK, policy)
        return SessionFlowDecision(FlowKind.PLANNER_REACT, policy)
