"""Single source for every production activity-type identifier (D10).

Activity types are the string contract between decision planners and the
activity registry. Handlers and planners must reference these constants —
string literals may only appear here and in tests, guarded by contract tests.
"""

from __future__ import annotations

RETRIEVAL_SEARCH = "retrieval.search"
MODEL_CALL = "model.call"
TOOL_CALL = "tool.call"
KNOWLEDGE_BUILD = "knowledge.build"
CHILD_RUN_START = "child_run.start"
PATROL_EXECUTE = "patrol.execute"
PATROL_VALIDATE = "patrol.validate"
REMEDIATION_EXECUTE = "remediation.execute"

ALL_ACTIVITY_TYPES: frozenset[str] = frozenset(
    {
        RETRIEVAL_SEARCH,
        MODEL_CALL,
        TOOL_CALL,
        KNOWLEDGE_BUILD,
        CHILD_RUN_START,
        PATROL_EXECUTE,
        PATROL_VALIDATE,
        REMEDIATION_EXECUTE,
    }
)

__all__ = [
    "ALL_ACTIVITY_TYPES",
    "CHILD_RUN_START",
    "KNOWLEDGE_BUILD",
    "MODEL_CALL",
    "PATROL_EXECUTE",
    "PATROL_VALIDATE",
    "REMEDIATION_EXECUTE",
    "RETRIEVAL_SEARCH",
    "TOOL_CALL",
]
