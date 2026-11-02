"""Authoritative recovery algorithm unit boundaries; no SQL or runtime proof."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from scripts.execution_capacity.observers import RecoveryJournal
from scripts.execution_capacity.persistence import PersistedFacts

from app.application.execution.orchestrator import CommandResult
from app.domain.execution.commands import CommandEnvelope
from app.domain.models.scope import OwnerScope


def test_pending_stable_settlement_replays_original_claim_and_envelope(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        run, activity = uuid4(), uuid4()
        identity = uuid5(NAMESPACE_URL, f"opencitadel:{activity}:CompleteActivity")
        envelope = CommandEnvelope(
            command_id=identity,
            command_type="CompleteActivity",
            command_schema_version=2,
            stream_type="run",
            stream_id=str(run),
            owner_user_id="test",
            team_id=None,
            correlation_id=run,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload={"activity_id": str(activity), "generation": 0, "claim_generation": 1},
        )
        journal.intent("command", identity, envelope.model_dump(mode="json"))

        class Facts(PersistedFacts):
            finished = False

            async def task(self, _):
                return {
                    "aggregate_id": str(run),
                    "status": "succeeded" if self.finished else "call_started",
                    "claim_generation": 1,
                }

            async def command(self, command_id, run_id):
                return (envelope, "received") if command_id == identity else None

            async def events(self, run_id):
                return [SimpleNamespace(causation_id=identity, event_type="ActivityCompleted")]

        facts = Facts(None, None, journal, OwnerScope.personal("test"), None)

        class Handler:
            async def handle(self, actual):
                assert actual is envelope
                assert actual.payload["claim_generation"] == 1
                facts.finished = True
                return CommandResult(
                    command_id=identity,
                    status="accepted",
                    first_event_position=4,
                    last_event_position=4,
                    rejection_code=None,
                )

        assert asyncio.run(facts.reconcile_activity(activity, Handler())) is True


def test_missing_outcome_after_claim_is_not_recreated_from_result_object(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        activity, run = uuid4(), uuid4()

        class Facts(PersistedFacts):
            async def task(self, _):
                return {
                    "aggregate_id": str(run),
                    "status": "call_started",
                    "claim_generation": 1,
                    "result_ref": "some-owned-object",
                }

            async def command(self, *args):
                return None

        facts = Facts(None, None, journal, OwnerScope.personal("test"), None)
        with pytest.raises(ValueError, match="unresolved recovery"):
            asyncio.run(facts.reconcile_activity(activity, None))
