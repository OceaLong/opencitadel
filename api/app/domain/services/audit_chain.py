"""Immutable audit hash chain utilities."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domain.models.audit_log import AuditLog

GENESIS = "0" * 64


def shard_key_for(*, team_id: str | None, actor_user_id: str | None) -> str:
    """Chain-shard identity for an audit row.

    The audit hash chain is partitioned so that unrelated writers do not queue
    behind one global lock. Team-scoped rows share one per-team chain, personal
    rows share one per-actor chain, and rows with neither (system-recorded
    events) fall back to a single ``system`` shard. Each shard is an
    independent ``prev_hash -> entry_hash`` chain seeded from :data:`GENESIS`.

    The shard binding is tamper-evident without a dedicated signed field:
    ``entry_fields`` already signs both ``team_id`` and ``actor_user_id``, and
    the shard key is a pure function of those two, so moving a row to another
    shard changes its signed content.
    """
    if team_id:
        return f"team:{team_id}"
    if actor_user_id:
        return f"user:{actor_user_id}"
    return "system"


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
    session_id: str | None = None,
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
        "session_id": session_id,
        "request_id": request_id,
        "metadata": metadata or {},
        "created_at": created_iso,
    }


def compute_entry_hash(secret: str, entry: dict[str, Any], prev_hash: str) -> str:
    msg = canonical(entry) + "|" + prev_hash
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_chain_logs(
    logs: list[AuditLog],
    keys: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Verify a set of chained audit logs, per shard.

    Each shard (see :func:`shard_key_for`) is its own hash chain seeded from
    :data:`GENESIS`; a row's ``prev_hash`` must equal the previous verified
    ``entry_hash`` *within the same shard*. Tracking the expected previous hash
    per shard makes verification independent of how shards are interleaved in
    ``logs`` (they only need to be in ascending chain order within each shard),
    so admin/auditor cross-tenant reads verify every involved shard on its own
    while still returning a single aggregate result.

    The first row that fails (missing sequence/hash, a broken ``prev_hash``
    link for its shard, or an ``entry_hash`` that no candidate signing key
    reproduces) sets ``first_broken_seq`` and stops the walk.
    """
    prev_by_shard: dict[str, str] = {}
    first_broken: int | None = None
    for log in logs:
        shard = shard_key_for(team_id=log.team_id, actor_user_id=log.actor_user_id)
        expected_prev = prev_by_shard.get(shard, GENESIS)
        if log.chain_seq is None or not log.entry_hash:
            first_broken = log.chain_seq
            break
        if log.prev_hash != expected_prev:
            first_broken = log.chain_seq
            break
        fields = entry_fields(
            chain_seq=log.chain_seq,
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_ip=log.actor_ip,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            team_id=log.team_id,
            session_id=log.session_id,
            request_id=log.request_id,
            metadata=log.metadata,
            created_at=log.created_at,
        )
        candidates = keys.get(log.signing_key_id, ())
        if not candidates or not any(
            compute_entry_hash(secret, fields, expected_prev) == log.entry_hash
            for secret in candidates
        ):
            first_broken = log.chain_seq
            break
        prev_by_shard[shard] = log.entry_hash
    return {
        "ok": first_broken is None,
        "total": len(logs),
        "first_broken_seq": first_broken,
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
