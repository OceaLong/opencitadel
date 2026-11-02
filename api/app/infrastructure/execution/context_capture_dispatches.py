"""Copy canonical native request and billing evidence from accepted physical receipts."""

from sqlalchemy import text

from app.application.execution import activity_types
from app.application.execution.content_sanitization import sanitize_content
from app.application.execution.native_request_evidence import reconstruct_native_request
from app.domain.models.resource_pin import ResourceUnavailable


async def context_dispatch_evidence(session, task, *, scope_key, summary=None):
    if task.activity_type not in {
        activity_types.MODEL_CALL,
        activity_types.CONTEXT_COMPACT,
    } or not any(task.request_payload.get(key) for key in ("context_ref", "prepare_ref")):
        return None
    rows = (
        (
            await session.execute(
                text("""
SELECT d.call_identity,d.configuration_id,d.logical_group,d.ordinal,d.claim_generation,
 d.request_snapshot,c.body AS configuration,c.purpose,s.fact AS settlement
 FROM execution_model_dispatches d
 JOIN execution_configurations c ON c.scope_key=d.scope_key AND c.id=d.configuration_id
 LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
 WHERE d.scope_key=:scope AND d.run_id=CAST(:run AS uuid)
 AND d.activity_id=:activity AND d.generation=:generation
 ORDER BY d.claim_generation,d.logical_group,d.ordinal LIMIT 129
"""),
                {
                    "scope": scope_key,
                    "run": task.aggregate_id,
                    "activity": task.activity_id,
                    "generation": task.request_generation,
                },
            )
        )
        .mappings()
        .all()
    )
    if not rows and task.activity_type == activity_types.CONTEXT_COMPACT:
        row = (
            (
                await session.execute(
                    text("""
SELECT l.version_id,l.slot_id,l.match_key,s.body
 FROM evaluation_replay_ledger l
 JOIN evaluation_replay_bindings b ON b.scope_key=l.scope_key AND b.run_id=l.run_id AND b.version_id=l.version_id
 JOIN evaluation_recording_slots s ON s.scope_key=l.scope_key AND s.version_id=l.version_id AND s.id=l.slot_id
 WHERE l.scope_key=:scope AND l.run_id=CAST(:run AS uuid) AND l.activity_id=:activity
 AND s.body->>'tool'='__context_compact__'
"""),
                    {"scope": scope_key, "run": task.aggregate_id, "activity": task.activity_id},
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            from app.application.evaluation.context_recording import summary_recording_contract
            from app.domain.evaluation.recording import recording_key

            contract = summary_recording_contract()
            if summary is None or row["match_key"] != recording_key(
                contract.name,
                contract.digest,
                {"request_digest": summary["request_digest"]},
                "root",
                task.request_payload["ordinal"],
                parallel_group=f"context:round:{task.request_payload['round_index']}",
            ):
                raise ResourceUnavailable("context replay capture differs")
            return {
                "format_version": 1,
                "calls": [],
                "replay": {
                    "version_id": str(row["version_id"]),
                    "slot_id": str(row["slot_id"]),
                    "match_key": row["match_key"],
                    "source_step_id": row["body"].get("source_step_id"),
                },
            }
    if not rows or len(rows) > 128:
        raise ResourceUnavailable("context physical capture unavailable")
    calls = []
    for row in rows:
        public = sanitize_content(dict(row))
        # Judge exactness after the final public sanitization. Numeric model
        # controls can be reconstructed; redacted user text and unknown fields cannot.
        exact = reconstruct_native_request(public["request_snapshot"]) is not None
        calls.append({**public, "native_payload_exact": exact})
    return {"format_version": 1, "calls": calls}
