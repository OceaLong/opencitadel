"""Formal recovery obligations and retained collections protect original bytes."""

from sqlalchemy import text

from app.domain.execution.run import TERMINAL_STATUSES
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


def has_execution_obligation(state):
    return (
        state.status not in TERMINAL_STATUSES
        or bool(state.active_activity_ids)
        or state.pending_approval_id is not None
        or any(
            effect.state not in {"applied", "not_applied"}
            or effect.awaiting_result
            or effect.integrity_conflict
            for effect in state.effect_operations
        )
    )


async def collection_is_retained(db, row, *, now, retention, grace):
    """Caller holds the scope append lock and the candidate's upload fence.

    Keep a whole historical session when any Run remains a root. This closes
    summary/checkpoint/recording dependencies without treating a summary as an
    original. Oversized closures stay retained; the caller bounds transaction time.
    """
    scope, run = row["scope_key"], row["run_id"]
    params = {"scope": scope, "run": str(run)}
    origin = (
        await db.execute(
            text("""
SELECT public_payload FROM execution_events WHERE owner_scope_key=:scope
AND stream_type='run' AND stream_id=:run AND stream_version=1
"""),
            params,
        )
    ).scalar_one_or_none()
    collection = (
        origin.get("source_entity_id")
        if origin and origin.get("source_entity_type") == "session"
        else None
    )
    params["collection"] = collection
    if await db.scalar(
        text("""
SELECT EXISTS(SELECT 1 FROM execution_command_inbox WHERE
(CASE WHEN team_id IS NOT NULL THEN 'team:'||team_id ELSE 'user:'||owner_user_id END)=:scope
AND status IN ('received','processing','dead_lettered') AND stream_type='run'
AND (stream_id=:run OR payload->>'parent_run_id'=:run OR (CAST(:collection AS text) IS NOT NULL
AND payload->>'source_entity_type'='session' AND payload->>'source_entity_id'=:collection)))
"""),
        params,
    ):
        return True, "pending_command"
    if origin is None:
        if await db.scalar(
            text("""
SELECT EXISTS(SELECT 1 FROM execution_events WHERE owner_scope_key=:scope AND stream_type='run'
AND stream_version=1 AND public_payload->>'parent_run_id'=:run)
"""),
            params,
        ):
            return True, "referenced_unpublished"
        return row["created_at"] >= now - grace, "unpublished_grace" if row[
            "created_at"
        ] >= now - grace else "orphan"
    if origin.get("family") not in {"agent", "ask"}:
        # Other execution families have their own artifact/evaluation retention.
        return True, "non_context_family"
    if collection and await db.scalar(
        text("""
SELECT EXISTS(SELECT 1 FROM sessions WHERE id=:collection
AND (CASE WHEN team_id IS NOT NULL THEN 'team:'||team_id ELSE 'user:'||owner_user_id END)=:scope)
"""),
        params,
    ):
        # Soft deletion is still restorable and therefore still a root.
        return True, "retained_session"
    owner, team = (scope[5:], None) if scope.startswith("user:") else (None, scope[5:])
    # Session and parent/child dependencies form a formal closure. Deduplication
    # also handles malformed cycles; oversized closures remain retained.
    related = (
        await db.execute(
            text("""
SELECT c.run_id,e.public_payload FROM public.opencitadel_object_collection(:scope,CAST(:run AS uuid)) c
LEFT JOIN execution_events e ON e.owner_scope_key=:scope AND e.stream_type='run'
 AND e.stream_version=1 AND e.stream_id=c.run_id::text LIMIT 10001
"""),
            params,
        )
    ).all()
    if len(related) > 10000:
        return True, "collection_limit"
    related_ids = {str(item.run_id) for item in related}
    for identity, origin in related:
        if origin is None:
            return True, "missing_related_origin"
        parent = origin.get("parent_run_id")
        if parent and parent not in related_ids:
            return True, "missing_parent"
        if origin.get("family") not in {"agent", "ask"}:
            return True, "non_context_family"
        related_collection = (
            origin.get("source_entity_id")
            if origin.get("source_entity_type") == "session"
            else None
        )
        if related_collection and await db.scalar(
            text("""
SELECT EXISTS(SELECT 1 FROM sessions WHERE id=:collection
AND (CASE WHEN team_id IS NOT NULL THEN 'team:'||team_id ELSE 'user:'||owner_user_id END)=:scope)
 OR EXISTS(SELECT 1 FROM execution_command_inbox WHERE
 (CASE WHEN team_id IS NOT NULL THEN 'team:'||team_id ELSE 'user:'||owner_user_id END)=:scope
 AND status IN ('received','processing','dead_lettered') AND stream_type='run'
 AND payload->>'source_entity_type'='session' AND payload->>'source_entity_id'=:collection)
"""),
            {"scope": scope, "collection": related_collection},
        ):
            return True, "retained_collection"
        identity = str(identity)
        p = {"scope": scope, "run": identity}
        if await db.scalar(
            text("""
SELECT EXISTS(SELECT 1 FROM execution_command_inbox WHERE
 (CASE WHEN team_id IS NOT NULL THEN 'team:'||team_id ELSE 'user:'||owner_user_id END)=:scope
 AND stream_type='run' AND (stream_id=CAST(:run AS text) OR payload->>'parent_run_id'=CAST(:run AS text)) AND status IN ('received','processing','dead_lettered'))
 OR EXISTS(SELECT 1 FROM resource_pins p JOIN execution_public_content c
 ON p.resource_kind='execution_content' AND p.resource_id=c.content_id::text
 AND p.resource_version=c.content_digest AND p.scope_key=c.scope_key
 WHERE p.scope_key=:scope AND p.available AND c.run_id=CAST(:run AS uuid))
 OR EXISTS(SELECT 1 FROM evaluation_recording_jobs WHERE scope_key=:scope AND source_run_id=CAST(:run AS uuid) AND status IN ('queued','running'))
 OR EXISTS(SELECT 1 FROM evaluation_batch_attempts WHERE scope_key=:scope AND run_id=CAST(:run AS uuid))
 OR EXISTS(SELECT 1 FROM evaluation_judge_intents WHERE scope_key=:scope AND run_id=CAST(:run AS uuid))
 OR EXISTS(SELECT 1 FROM execution_effect_requests WHERE scope_key=:scope AND run_id=CAST(:run AS uuid) AND status IN ('pending','running'))
"""),
            p,
        ):
            return True, "retained_evidence"
        loaded = await PostgresVerifiedRunLoader(db).load(identity, owner_scope=(owner, team))
        if has_execution_obligation(loaded.replay.state):
            return True, "execution_obligation"
        # The most recent formal fact includes late settlements; it is more
        # conservative than a projection's terminal timestamp.
        latest = await db.scalar(
            text("""
SELECT occurred_at FROM execution_events WHERE owner_scope_key=:scope AND stream_type='run'
AND stream_id=:run ORDER BY stream_version DESC LIMIT 1
"""),
            {"scope": scope, "run": identity},
        )
        if latest is None or latest >= now - retention:
            return True, "terminal_retention"
    return False, "retention_expired"
