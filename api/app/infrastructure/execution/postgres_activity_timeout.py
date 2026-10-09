"""Reject unsafe timeout intent and durably request existing unknown-outcome protocol.

The original v1 timeout command/envelope remains unchanged. Caller holds the same
owner append lock used by call-start and outcomes before loading accepted state.
"""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from app.application.execution.activity_registry import UnknownActivityTypeError
from app.application.execution.activity_types import TOOL_CALL
from app.domain.execution.commands import CommandEnvelope
from app.infrastructure.execution.postgres_inbox import PostgresInbox


class PostgresActivityTimeoutGuard:
    def __init__(self, registry):
        self.registry = registry

    @staticmethod
    def applies(command):
        return (
            command.stream_type == "run"
            and command.command_type == "FailActivity"
            and command.payload.get("failure_code")
            in {"ACTIVITY_TIMEOUT", "ACTIVITY_DEAD_LETTERED"}
        )

    async def redirect(self, session, command, state):
        if not self.applies(command):
            return False
        activity = UUID(command.payload["activity_id"])
        generation = command.payload["generation"]
        if (
            activity not in state.active_activity_ids
            or generation != state.retry_generation
            or activity not in state.started_activity_ids
        ):
            return False
        kind = next(
            (
                kind
                for identity, kind, gen in state.requested_activities
                if identity == activity and gen == generation
            ),
            None,
        )
        try:
            handler = self.registry.resolve(kind or "")
        except UnknownActivityTypeError:
            handler = None
        if handler is not None and handler.idempotent:
            return False
        if kind == TOOL_CALL and state.source_entity_type == "evaluation_recorded_case":
            # Kernel-only immutable prebinding proves this invocation had no real-tool
            # fallback. It does not grant permission for another replay/invocation.
            binding = (
                (
                    await session.execute(
                        text(
                            "SELECT b.admission,b.principal,c.body FROM evaluation_replay_bindings b JOIN evaluation_config_versions c ON c.scope_key=b.scope_key AND c.id=CAST(b.admission->>'config_version_id' AS uuid) WHERE b.run_id=:run AND b.owner_user_id IS NOT DISTINCT FROM :owner AND b.team_id IS NOT DISTINCT FROM :team"
                        ),
                        {
                            "run": state.run_id,
                            "owner": command.owner_user_id,
                            "team": command.team_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if binding:
                proof = binding["admission"]
                if (
                    proof.get("source_entity_id") == state.source_entity_id
                    and proof.get("source_entity_type") == state.source_entity_type
                    and proof.get("policy_digest") == state.policy_snapshot.snapshot_digest
                    and proof.get("config_fingerprint") == binding["body"]["fingerprint"]
                    and proof.get("purpose") == "evaluation_subject"
                    and binding["principal"].get("user_id")
                ):
                    return False
        unknown = CommandEnvelope(
            command_id=uuid5(
                NAMESPACE_URL, f"opencitadel:timeout-unknown:{state.run_id}:{activity}:{generation}"
            ),
            command_type="MarkActivityOutcomeUnknown",
            command_schema_version=1,
            stream_type="run",
            stream_id=str(state.run_id),
            expected_stream_version=None,
            owner_user_id=command.owner_user_id,
            team_id=command.team_id,
            correlation_id=state.run_id,
            causation_id=None,
            issued_at=command.issued_at,
            payload={
                "activity_id": str(activity),
                "generation": generation,
                "failure_code": "NON_IDEMPOTENT_OUTCOME_UNKNOWN",
            },
        )
        await PostgresInbox(session).receive(unknown)
        return True
