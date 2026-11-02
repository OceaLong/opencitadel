"""Reconstruct only typed numerical controls; all other redaction is irreversible."""

import math
from copy import deepcopy

from app.domain.execution.context_models import context_digest

_NUMERIC_PATHS = {
    ("temperature",),
    ("max_tokens",),
    ("max_completion_tokens",),
    ("top_p",),
    ("top_k",),
    ("seed",),
    ("generationConfig", "temperature"),
    ("generationConfig", "maxOutputTokens"),
    ("generationConfig", "topP"),
    ("generationConfig", "topK"),
    ("thinking", "budget_tokens"),
}


def reconstruct_native_request(snapshot):
    """Return exact canonical payload or None, never a partially repaired request."""
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("omitted_field_count") != 0
        or not snapshot.get("native_payload_digest")
        or not isinstance(snapshot.get("numeric_parameters", []), list)
    ):
        return None
    native = deepcopy(snapshot.get("request"))
    if not isinstance(native, dict):
        return None
    seen = set()
    for parameter in snapshot.get("numeric_parameters", []):
        if not isinstance(parameter, dict) or not isinstance(parameter.get("path"), list):
            return None
        path = parameter["path"]
        if not all(isinstance(key, str) for key in path):
            return None
        path = tuple(path)
        value = parameter.get("value")
        if path not in _NUMERIC_PATHS or path in seen or type(value) not in (int, float):
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        seen.add(path)
        parent = native if len(path) == 1 else native.get(path[0])
        if not isinstance(parent, dict) or path[-1] not in parent:
            return None
        if parent[path[-1]] != "[redacted]" and parent[path[-1]] != value:
            return None
        parent[path[-1]] = value
    return native if context_digest(native) == snapshot["native_payload_digest"] else None
