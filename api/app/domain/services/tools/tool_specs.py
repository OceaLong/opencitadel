"""Declarative identity of every production tool pack (D10).

``ToolSpec`` is the domain-side declaration: which session modes expose a
pack and whether it participates in retrieval (replacing the historic
``"kb_search"`` string special-case in the catalog). The pack factories need
application-layer dependencies, so the assembly table pairing each spec with
its builder lives next to ``AgentToolCatalog`` — this module owns only the
layer-pure declaration.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models.session_mode import SessionMode

ALL_MODES: frozenset[SessionMode] = frozenset(SessionMode)
AGENT_ONLY: frozenset[SessionMode] = frozenset({SessionMode.AGENT})


@dataclass(frozen=True)
class ToolSpec:
    """One tool pack's declarative registration entry.

    ``retrieval_tool`` names the pack's retrieval entry point; packs without
    one do not participate in ``retrieve()``.
    """

    name: str
    modes: frozenset[SessionMode]
    retrieval_tool: str | None = None


__all__ = ["AGENT_ONLY", "ALL_MODES", "ToolSpec"]
