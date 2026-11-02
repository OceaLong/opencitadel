"""Summary response persistence is fenced and backed by settled F07 receipts."""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.application.execution.context_compaction import (
    require_proposal_binding,
    require_summary_lineage,
)
from app.domain.execution.context_summary import ContextSummaryProposal
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.context_summary_models import proposals
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


class PostgresContextSummaryProposalStore:
    def __init__(self, session):
        self.session = session

    async def load(self, request, run):
        scope = run.owner_scope
        key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
        body = await self.session.scalar(
            select(proposals.c.body).where(
                proposals.c.scope_key == key,
                proposals.c.activity_id == request.activity_id,
                proposals.c.run_id == run.run_id,
            )
        )
        if body is None:
            return None
        return require_proposal_binding(ContextSummaryProposal.model_validate(body), request, run)

    async def save(self, proposal, request, context):
        require_proposal_binding(proposal, request, context.run)
        key = PostgresEventStore._scope_advisory_lock_key(context.owner_user_id, context.team_id)
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        loaded = await PostgresVerifiedRunLoader(self.session).load(
            str(proposal.run_id),
            owner_scope=(context.owner_user_id, context.team_id),
            required_pin=proposal.definition_pin,
        )
        state = loaded.replay.state
        require_summary_lineage(state, request)
        if (
            request.activity_id not in state.active_activity_ids
            or (request.activity_id, request.generation, context.claim_generation)
            not in state.started_activity_claims
        ):
            raise ValueError("CONTEXT_SUMMARY_LEASE_UNAVAILABLE")
        params = {
            "scope": proposal.scope_key,
            "run": proposal.run_id,
            "activity": request.activity_id,
            "generation": request.generation,
            "claim": context.claim_generation,
            "worker": context.worker_id,
            "digest": request.input_digest,
            "payload": canonical_json_bytes(request.input_payload).decode(),
        }
        # Serialize with reclaim/completion, then check wall-clock deadlines.
        valid = await self.session.scalar(
            text("""
SELECT 1 FROM execution_activity_tasks WHERE activity_id=:activity AND aggregate_id=CAST(CAST(:run AS uuid) AS text)
 AND request_generation=:generation AND claim_generation=:claim AND claimed_by=:worker
 AND status='call_started' AND request_digest=:digest AND activity_type='context.compact'
 AND request_payload=CAST(:payload AS jsonb) FOR SHARE
"""),
            params,
        )
        if not valid:
            raise ValueError("CONTEXT_SUMMARY_LEASE_UNAVAILABLE")
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": proposal.scope_key + ":summary:" + str(proposal.logical_id)},
        )
        valid = await self.session.scalar(
            text("""
SELECT 1 FROM execution_activity_tasks WHERE activity_id=:activity
 AND claim_deadline>clock_timestamp() AND timeout_at>clock_timestamp()
"""),
            params,
        )
        if not valid:
            raise ValueError("CONTEXT_SUMMARY_LEASE_UNAVAILABLE")
        for identity in proposal.physical_call_ids:
            evidence = (
                (
                    await self.session.execute(
                        text("""
SELECT c.body,c.purpose FROM execution_model_dispatches d
 JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
 JOIN execution_configurations c ON c.scope_key=d.scope_key AND c.id=d.configuration_id
 WHERE d.scope_key=:scope AND d.run_id=:run AND d.activity_id=:activity AND d.call_identity=:identity
 AND d.generation=:generation AND d.claim_generation=:claim
"""),
                        {**params, "identity": str(identity)},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if evidence is None or evidence["purpose"] != proposal.purpose:
                raise ValueError("CONTEXT_SUMMARY_PHYSICAL_RECEIPT_UNAVAILABLE")
            detail = evidence["body"].get("context_summary")
            if detail != {
                "logical_id": str(proposal.logical_id),
                "chunk_digest": proposal.chunk_digest,
                "namespace_id": str(proposal.namespace_id) if proposal.namespace_id else None,
            }:
                raise ValueError("CONTEXT_SUMMARY_PHYSICAL_RECEIPT_MISMATCH")
        await self.session.execute(
            insert(proposals)
            .values(
                scope_key=proposal.scope_key,
                activity_id=proposal.activity_id,
                run_id=proposal.run_id,
                logical_id=proposal.logical_id,
                body=proposal.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
        )
        if await self.load(request, context.run) != proposal:
            raise ValueError("CONTEXT_SUMMARY_PROPOSAL_CONFLICT")
        # Immutable receipt/uniqueness locks can also wait. A response saved
        # after the lease expired must roll back with this same transaction.
        valid = await self.session.scalar(
            text("""
SELECT 1 FROM execution_activity_tasks WHERE activity_id=:activity
 AND claim_deadline>clock_timestamp() AND timeout_at>clock_timestamp()
"""),
            params,
        )
        if not valid:
            raise ValueError("CONTEXT_SUMMARY_LEASE_UNAVAILABLE")


class DurableContextSummaryProposalStore:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory
        self.authorization = AuthorizationContext.system("execution-kernel")

    async def load(self, request, run):
        async with self.uow_factory(self.authorization) as work:
            return await PostgresContextSummaryProposalStore(work.db_session).load(request, run)

    async def save(self, proposal, request, context):
        async with self.uow_factory(self.authorization) as work:
            await PostgresContextSummaryProposalStore(work.db_session).save(
                proposal, request, context
            )
            await work.commit()
