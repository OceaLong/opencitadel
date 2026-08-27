"""Immutable audit hash chain utilities."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64
ADVISORY_LOCK_KEY = 0x0A0D17C4


def canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def entry_fields(
    *,
    chain_seq: int,
    id: str,
    actor_user_id: str | None,
    actor_ip: str,
    action: str,
    resource_type: str,
    resource_id: str,
    team_id: str | None,
    request_id: str,
    metadata: dict[str, Any],
    created_at: datetime,
) -> dict[str, Any]:
    if created_at.tzinfo is None:
        raise ValueError("audit entry created_at must be timezone-aware")
    created_iso = created_at.astimezone(UTC).isoformat()
    return {
        "chain_seq": chain_seq,
        "id": id,
        "actor_user_id": actor_user_id,
        "actor_ip": actor_ip,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "team_id": team_id,
        "request_id": request_id,
        "metadata": metadata or {},
        "created_at": created_iso,
    }


def compute_entry_hash(secret: str, entry: dict[str, Any], prev_hash: str) -> str:
    msg = canonical(entry) + "|" + prev_hash
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
