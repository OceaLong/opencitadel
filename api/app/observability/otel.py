"""Re-export shim over ``core.otel`` (D14/K4-5).

The observability helpers moved to ``core.otel`` so the infrastructure layer
can use them without importing an ``app``-root module (import-linter forbids
``app.infrastructure`` → ``app.observability``). Application-layer and
process-entry callers keep this import path.
"""

from core.otel import (
    get_tracer,
    record_agent_cancel,
    record_agent_step,
    record_llm_tokens,
    setup_observability,
)

__all__ = [
    "get_tracer",
    "record_agent_cancel",
    "record_agent_step",
    "record_llm_tokens",
    "setup_observability",
]
