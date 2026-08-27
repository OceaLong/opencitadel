"""Deterministic serialization for replay and integrity checks."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from app.domain.execution.commands import normalize_utc


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical state contains a non-finite number")
        return value
    if isinstance(value, datetime):
        return normalize_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical state dictionaries require string keys")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonical_value(item) for item in value]
        return sorted(canonical_items, key=_sort_key)
    raise TypeError(f"unsupported canonical state value: {type(value).__name__}")


def _sort_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical_value(value),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def canonical_state_hash(state: BaseModel) -> str:
    return hashlib.sha256(canonical_json_bytes(state)).hexdigest()


__all__ = ["canonical_json_bytes", "canonical_state_hash"]
