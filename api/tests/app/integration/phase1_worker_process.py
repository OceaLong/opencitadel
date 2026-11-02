"""Subprocess entry point for real PostgreSQL Activity ownership tests."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.execution.activity_registry import create_activity_registry
from app.application.execution.activity_worker import ActivityWorker
from app.application.execution.run_service import RunService
from app.domain.execution.activity import ActivityOutcome
from app.domain.execution.commands import CommandEnvelope
from app.domain.execution.run import RunAggregate
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_kernel_database_uri,
)


async def main(options):
    engine = create_async_engine(
        execution_kernel_database_uri(),
        connect_args={
            "server_settings": {"application_name": f"phase1-worker-{options['worker']}"}
        },
    )
    sessions = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    auth = AuthorizationContext.system("phase1-worker-process")
    identities = []
    claims = []
    sent = asyncio.Event()

    class FaultStore(PostgresActivityStore):
        renewals = 0

        async def claim(self, **kwargs):
            claimed = await super().claim(**kwargs)
            identities.extend(c.lease_proof.model_dump(mode="json") for c in claimed)
            claims.extend(claimed)
            return claimed

        async def heartbeat(self, claim, **kwargs):
            self.renewals += 1
            fault = options.get("fault")
            if options.get("fault_after_send") and not sent.is_set():
                return await super().heartbeat(claim, **kwargs)
            if fault == "false":
                return None
            if fault == "timeout":
                # A physical database round-trip outlives the heartbeat budget;
                # the production supervisor must cancel it and stop permission.
                async with sessions() as db:
                    await db.execute(
                        text("SELECT pg_sleep(:seconds)"),
                        {"seconds": max(2, options.get("ttl", 0.9) * 2)},
                    )
            if fault == "transient" and self.renewals == 1:
                async with sessions() as db:
                    await db.execute(text("SELECT 1 / 0"))
            return await super().heartbeat(claim, **kwargs)

    class Handler:
        activity_type = "phase1.probe"
        idempotent = options.get("idempotent", False)

        async def execute(self, request, context):
            context.require_active_lease()
            async with sessions() as db:
                sent_at = await db.scalar(
                    text(
                        "INSERT INTO phase1_physical_calls(activity_id,worker_id,operation_key) VALUES(:id,:worker,:key) RETURNING sent_at"
                    ),
                    {
                        "id": request.activity_id,
                        "worker": context.worker_id,
                        "key": context.idempotency_key,
                    },
                )
                await db.commit()
            sent.set()
            if options.get("hold_until"):
                await asyncio.sleep(
                    max(
                        0, (datetime.fromisoformat(options["hold_until"]) - sent_at).total_seconds()
                    )
                    + 0.1
                )
            await asyncio.sleep(options.get("delay", 0.05))
            context.require_active_lease()
            return ActivityOutcome.succeeded(result_ref=None, result_summary="physical probe")

    store = FaultStore(session_factory=sessions, authorization=auth)
    orchestrator = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, aggregates={"run": RunAggregate()}, authorization=auth
    )
    worker = ActivityWorker(
        store=store,
        run_contexts=PostgresRunContextSource(session_factory=sessions, authorization=auth),
        run_service=RunService(orchestrator=orchestrator),
        registry=create_activity_registry(Handler()),
        worker_id=options["worker"],
        claim_ttl=timedelta(seconds=options.get("ttl", 15)),
        max_concurrency=options.get("slots", 8),
    )
    try:
        stats = await worker.run_once(now=datetime.now(UTC), limit=options.get("limit", 100))
        late = None
        if options.get("retry_stale") and stats.stale:
            claim = claims[0]
            # A delayed caller still holding the old token attempts a fresh
            # submission after local supervision stopped its first transaction.
            late = await orchestrator.handle(
                CommandEnvelope(
                    command_id=uuid4(),
                    command_type="CompleteActivity",
                    command_schema_version=2,
                    stream_type="run",
                    stream_id=claim.request.aggregate_id,
                    owner_user_id=claim.owner_user_id,
                    team_id=claim.team_id,
                    correlation_id=claim.request.activity_id,
                    causation_id=claim.request.activity_id,
                    issued_at=datetime.now(UTC),
                    activity_lease_proof=claim.lease_proof,
                    payload={
                        "activity_id": str(claim.request.activity_id),
                        "generation": claim.request.generation,
                        "claim_generation": claim.claim_generation,
                        "result_summary": "delayed physical result",
                    },
                )
            )
        print(
            json.dumps(
                {
                    "stats": stats.model_dump(),
                    "claims": identities,
                    "renewals": store.renewals,
                    "late_status": late.status if late else None,
                }
            ),
            flush=True,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.argv[1])))
