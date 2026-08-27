"""Canonical serialization for immutable Runtime Policy revisions."""

import hashlib
import json

from pydantic import BaseModel, JsonValue

from app.domain.json_values import validate_json


def canonical_policy_bytes(
    schema_version: int,
    payload: BaseModel | dict[str, JsonValue],
) -> bytes:
    if isinstance(schema_version, bool) or schema_version < 1:
        raise ValueError("schema_version must be a positive integer")
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    validate_json(body, path="policy")
    try:
        serialized = json.dumps(
            {"schema_version": schema_version, "payload": body},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except ValueError as exc:
        raise ValueError("policy values must be finite JSON") from exc
    return serialized.encode("utf-8")


def policy_digest(
    schema_version: int,
    payload: BaseModel | dict[str, JsonValue],
) -> str:
    digest = hashlib.sha256(canonical_policy_bytes(schema_version, payload)).hexdigest()
    return f"sha256:{digest}"


__all__ = ["canonical_policy_bytes", "policy_digest"]
