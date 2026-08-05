"""Deterministic output limits applied before serialization.

Mirrored from ops-collector/src/opencitadel_ops_collector/limits.py (the two
services are deployed independently and do not share a package); adapted
only to accept ActuatorSettings instead of CollectorSettings, since both
settings classes expose the same max_string_chars/max_array_items/max_rows/
max_output_bytes fields.
"""
from __future__ import annotations

import json
from typing import Any

from .config import ActuatorSettings


def bound(value: Any, settings: ActuatorSettings) -> tuple[Any, list[str]]:
    warnings: list[str] = []

    def visit(item: Any) -> Any:
        if isinstance(item, str) and len(item) > settings.max_string_chars:
            warnings.append("string_truncated")
            return item[: settings.max_string_chars]
        if isinstance(item, list):
            if len(item) > settings.max_array_items:
                warnings.append("array_truncated")
            return [visit(child) for child in item[: settings.max_array_items]]
        if isinstance(item, dict):
            pairs = list(item.items())
            if len(pairs) > settings.max_rows:
                warnings.append("object_truncated")
            return {str(key): visit(child) for key, child in pairs[: settings.max_rows]}
        return item

    bounded = visit(value)
    encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > settings.max_output_bytes:
        raise ValueError("OUTPUT_TOO_LARGE")
    return bounded, sorted(set(warnings))
