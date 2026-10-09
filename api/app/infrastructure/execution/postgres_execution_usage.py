"""Receipt-authorized usage phases; public cuts never inspect later settlement."""

import asyncio
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from app.application.execution.view_facts import ProjectionFact
from app.domain.execution.commands import CommandEnvelope
from app.infrastructure.security.db_authorization import configure_session_authorization


def params(event):
    return {
        "identity": str(event.internal_payload["call_identity"]),
        "phase": event.internal_payload["phase"],
        "run": UUID(event.stream_id),
        "owner": event.owner_user_id,
        "team": event.team_id,
    }


async def authorize_usage(session, command):
    p = {
        "identity": str(command.payload["call_identity"]),
        "phase": command.payload["phase"],
        "run": UUID(command.stream_id),
        "owner": command.owner_user_id,
        "team": command.team_id,
    }
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "usage-publication:" + str(p["run"]) + ":" + p["identity"]},
    )
    return (
        (
            await session.execute(
                text("""SELECT d.*,p.event_position FROM execution_model_dispatches d
      LEFT JOIN execution_usage_publications p ON p.scope_key=d.scope_key AND p.call_identity=d.call_identity AND p.phase=:phase
      WHERE d.call_identity=:identity AND d.run_id=:run AND d.owner_user_id IS NOT DISTINCT FROM :owner AND d.team_id IS NOT DISTINCT FROM :team
      AND (:phase='dispatch' OR (EXISTS(SELECT 1 FROM execution_model_settlements s WHERE s.scope_key=d.scope_key AND s.call_identity=d.call_identity)
      AND EXISTS(SELECT 1 FROM execution_usage_publications q WHERE q.scope_key=d.scope_key AND q.call_identity=d.call_identity AND q.phase='dispatch')))"""),
                p,
            )
        )
        .mappings()
        .one_or_none()
    )


async def mark_usage_publication(session, event):
    await session.execute(
        text("""INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,team_id,created_by)
    VALUES (:identity,:phase,:event,:position,:owner,:team,'execution-usage')"""),
        {**params(event), "event": event.event_id, "position": event.position},
    )


async def project_usage(session, *, event, observation):
    p = {**params(event), "event": event.event_id, "position": event.position}
    row = (
        (
            await session.execute(
                text("""SELECT d.*,c.body,c.purpose,s.fact FROM execution_model_dispatches d
    JOIN execution_configurations c ON c.scope_key=d.scope_key AND c.id=d.configuration_id
    JOIN execution_usage_publications p ON p.scope_key=d.scope_key AND p.call_identity=d.call_identity AND p.phase=:phase AND p.event_id=:event AND p.event_position=:position
    LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity AND :phase='settlement'
    WHERE d.run_id=:run AND d.call_identity=:identity AND d.owner_user_id IS NOT DISTINCT FROM :owner AND d.team_id IS NOT DISTINCT FROM :team"""),
                p,
            )
        )
        .mappings()
        .one()
    )
    # Dispatch phase is ALWAYS unknown even if a later settlement already exists.
    fact = row["fact"] or {}
    usage = fact.get("usage", {})
    import json

    coverage = {
        "phase": p["phase"],
        "configuration_revision": row["configuration_id"],
        "version_unpinned": fact.get("version_unpinned", True),
        "categories": usage,
    }
    await session.execute(
        text("""INSERT INTO execution_usage_facts(id,call_identity,run_id,activity_id,attempt_id,purpose,model_revision,input_tokens,output_tokens,price_revision,cost_usd,coverage,occurred_at,revision,owner_user_id,team_id,created_by)
    VALUES (:id,:identity,:run,:activity,:attempt,:purpose,:model,:input,:output,:price,:cost,CAST(:coverage AS jsonb),:occurred,:revision,:owner,:team,'execution-usage')
    ON CONFLICT(scope_key,call_identity) DO UPDATE SET model_revision=EXCLUDED.model_revision,input_tokens=EXCLUDED.input_tokens,output_tokens=EXCLUDED.output_tokens,
    price_revision=EXCLUDED.price_revision,cost_usd=EXCLUDED.cost_usd,coverage=EXCLUDED.coverage,revision=EXCLUDED.revision
    WHERE execution_usage_facts.revision<EXCLUDED.revision"""),
        {
            **p,
            "id": UUID(p["identity"]),
            "activity": row["activity_id"],
            "attempt": row["attempt_id"],
            "purpose": row["purpose"],
            "model": fact.get("model_revision"),
            "input": usage.get("prompt_tokens"),
            "output": usage.get("completion_tokens"),
            "price": row["body"].get("price_revision"),
            "cost": Decimal(fact["cost_usd"]) if fact.get("cost_usd") is not None else None,
            "coverage": json.dumps(coverage),
            "occurred": row["created_at"],
            "revision": observation.projection_revision,
        },
    )
    # Known subtotals are labelled; an unknown call prevents complete totals.
    records = (
        (
            await session.execute(
                text(
                    "SELECT purpose,input_tokens,output_tokens,cost_usd FROM execution_usage_facts WHERE run_id=:run AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"
                ),
                p,
            )
        )
        .mappings()
        .all()
    )
    summary = {}
    for purpose in ("production", "evaluation_subject", "evaluation_judge", "unknown"):
        items = [r for r in records if r["purpose"] == purpose]
        if not items:
            continue
        unknown_tokens = sum(r["input_tokens"] is None or r["output_tokens"] is None for r in items)
        unknown_cost = sum(r["cost_usd"] is None for r in items)
        summary[purpose] = {
            "calls": len(items),
            "unknown_usage_calls": unknown_tokens,
            "unknown_cost_calls": unknown_cost,
            "known_input_count": sum(r["input_tokens"] or 0 for r in items),
            "known_output_count": sum(r["output_tokens"] or 0 for r in items),
            "known_cost_usd": str(sum((r["cost_usd"] or Decimal(0) for r in items), Decimal(0))),
        }
    body = row["body"]
    config = {
        "configuration_revision": row["configuration_id"],
        "model_revision": fact.get("model_revision"),
        "prompt_revision": body.get("prompt", {}).get("template_revision"),
        "tool_contract_revision": body.get("tools", {}).get("fingerprint"),
        "temperature": body.get("settings", {}).get("temperature"),
        "version_unpinned": fact.get("version_unpinned", True),
    }
    patch = {"usage": summary, "purpose": row["purpose"], "configuration": config}
    public = ProjectionFact(
        event.position, 0, None, "run", str(row["run_id"]), patch, "formal"
    ).playback_patch()
    observation.public_payload = {
        **observation.public_payload,
        "facts": [*observation.public_payload["facts"], public],
    }
    await session.execute(
        text(
            "UPDATE execution_view_runs SET purpose=:purpose,configuration=CAST(:configuration AS jsonb),configuration_revision=:config_id,model_revision=:model WHERE run_id=:run"
        ),
        {
            **p,
            "usage": json.dumps(summary),
            "purpose": row["purpose"],
            "configuration": json.dumps(config),
            "config_id": row["configuration_id"],
            "model": fact.get("model_revision"),
        },
    )
    await session.flush()


class ExecutionUsageMaintenance:
    def __init__(self, *, session_factory, authorization, handler, receipt_timeout=5):
        self.sessions, self.authorization, self.handler = session_factory, authorization, handler
        if receipt_timeout <= 0:
            raise ValueError("invalid usage receipt timeout")
        self.receipt_timeout = receipt_timeout

    async def process_pending(self, *, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError("invalid usage maintenance limit")
        async with self.sessions() as session:
            await configure_session_authorization(session, self.authorization)
            rows = (
                (
                    await session.execute(
                        text("""SELECT d.*,CASE WHEN p.call_identity IS NULL THEN 'dispatch' ELSE 'settlement' END AS phase
            FROM execution_model_dispatches d LEFT JOIN execution_usage_publications p ON p.scope_key=d.scope_key AND p.call_identity=d.call_identity AND p.phase='dispatch'
            LEFT JOIN execution_usage_delivery retry ON retry.scope_key=d.scope_key AND retry.call_identity=d.call_identity AND retry.phase=CASE WHEN p.call_identity IS NULL THEN 'dispatch' ELSE 'settlement' END
            WHERE (retry.call_identity IS NULL OR (NOT retry.quarantined AND retry.next_attempt_at<=clock_timestamp())) AND (p.call_identity IS NULL OR (EXISTS(SELECT 1 FROM execution_model_settlements s WHERE s.scope_key=d.scope_key AND s.call_identity=d.call_identity)
            AND NOT EXISTS(SELECT 1 FROM execution_usage_publications q WHERE q.scope_key=d.scope_key AND q.call_identity=d.call_identity AND q.phase='settlement')))
            ORDER BY d.created_at,d.call_identity LIMIT :limit"""),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        emitted = 0
        for row in rows:
            command = CommandEnvelope(
                command_id=uuid5(
                    NAMESPACE_URL,
                    "usage:" + row["scope_key"] + ":" + row["call_identity"] + ":" + row["phase"],
                ),
                command_type="RecordModelUsage",
                command_schema_version=1,
                stream_type="run",
                stream_id=str(row["run_id"]),
                owner_user_id=row["owner_user_id"],
                team_id=row["team_id"],
                correlation_id=row["run_id"],
                causation_id=None,
                issued_at=row["created_at"],
                payload={"call_identity": row["call_identity"], "phase": row["phase"]},
            )
            try:
                async with asyncio.timeout(self.receipt_timeout):
                    result = await self.handler.handle(command)
                if result.status == "accepted":
                    emitted += 1
                    continue
                error_code = "publication_" + result.status
            except Exception as error:  # noqa: BLE001 - isolate each durable receipt, preserving cancellation
                error_code = "timeout" if isinstance(error, TimeoutError) else "publication_error"
            # Operational retries are separate from immutable provider evidence.
            # Every new transaction establishes its own kernel authorization.
            async with self.sessions() as session:
                await configure_session_authorization(session, self.authorization)
                await session.execute(
                    text("""INSERT INTO execution_usage_delivery(call_identity,phase,failures,next_attempt_at,last_error_code,owner_user_id,team_id,created_by)
                    VALUES (:identity,:phase,1,clock_timestamp()+INTERVAL '1 second',:code,:owner,:team,'execution-usage')
                    ON CONFLICT(scope_key,call_identity,phase) DO UPDATE SET failures=execution_usage_delivery.failures+1,
                    next_attempt_at=clock_timestamp()+INTERVAL '1 second'*least(60,power(2,execution_usage_delivery.failures)),
                    quarantined=execution_usage_delivery.failures+1>=5,last_error_code=EXCLUDED.last_error_code,updated_at=clock_timestamp()"""),
                    {
                        "identity": row["call_identity"],
                        "phase": row["phase"],
                        "code": error_code,
                        "owner": row["owner_user_id"],
                        "team": row["team_id"],
                    },
                )
                await session.commit()
        return {"emitted": emitted}
