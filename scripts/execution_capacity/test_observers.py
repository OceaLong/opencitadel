"""Unit boundaries only: real object encoder, private journal and observers."""

import asyncio
from hashlib import sha256
from uuid import uuid4

import pytest
from scripts.execution_capacity.observers import ObservedStorage, RecoveryJournal

from app.application.execution.activity_inputs import ActivityObjectStore
from app.domain.external.object_storage import ObjectNotFoundError


class Storage:
    def __init__(self):
        self.values = {}
        self.fail = False

    async def put_bytes(self, key, data):
        if self.fail:
            raise TimeoutError("uncertain SDK completion")
        self.values[key] = data

    async def get_bytes(self, key):
        if key not in self.values:
            raise ObjectNotFoundError(key)
        return self.values[key]

    async def get_bounded_bytes(self, key, limit):
        from app.domain.external.object_storage import BoundedObjectBytes

        value = await self.get_bytes(key)
        return BoundedObjectBytes(value[:limit], len(value) > limit)

    async def delete_bytes(self, key):
        del self.values[key]


def test_actual_encoder_is_forwarded_after_durable_parent(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        run = str(uuid4())
        journal.intent("run", run, {"scope": "user:test"})
        storage = Storage()
        observed = ObservedStorage(storage, journal)
        ref, digest = asyncio.run(ActivityObjectStore(observed).put_input(run, {"message": "real"}))
        record = journal.get("object", ref)
        assert record["body"] == {
            "scope": "user:test",
            "parent_kind": "run",
            "parent": run,
            "sha256": digest,
            "size": len(storage.values[ref]),
        }
        assert record["receipt"]["sha256"] == sha256(storage.values[ref]).hexdigest()
        assert asyncio.run(observed.get_bytes(ref)) == storage.values[ref]
    with RecoveryJournal(tmp_path) as journal:
        assert journal.get("object", ref)["receipt"] is not None


def test_prefix_or_unknown_parent_never_authorizes_write(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        storage = Storage()
        with pytest.raises(ValueError, match="parent"):
            asyncio.run(
                ActivityObjectStore(ObservedStorage(storage, journal)).put_input(uuid4(), {})
            )
        assert storage.values == {}


def test_absent_object_after_uncertain_put_remains_unresolved(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        run = str(uuid4())
        journal.intent("run", run, {"scope": "user:test"})
        storage = Storage()
        storage.fail = True
        observed = ObservedStorage(storage, journal)
        with pytest.raises(TimeoutError):
            asyncio.run(ActivityObjectStore(observed).put_input(run, {}))
        key = next(journal.records("object"))[0]
        assert asyncio.run(observed.reconcile(key)) == "uncertain_absent"
        assert journal.get("object", key)["receipt"] is None
        with pytest.raises(ValueError, match="retained"):
            asyncio.run(observed.delete_bytes(key))


def test_recovery_cannot_replace_original_envelope(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        journal.intent("command", "exact", {"payload": {"claim": 1}})
        with pytest.raises(ValueError, match="differs"):
            journal.intent("command", "exact", {"payload": {"claim": 2}})
        assert journal.get("command", "exact")["body"]["payload"]["claim"] == 1


def test_admission_observer_preserves_scope_ceiling_and_exact_envelope(tmp_path):
    from datetime import UTC, datetime

    from scripts.execution_capacity.observers import ObservedSink

    from app.domain.execution.commands import CommandEnvelope

    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        run = uuid4()
        journal.intent("run", run, {"scope": "user:test"})
        command = CommandEnvelope(
            command_id=uuid4(),
            command_type="CreateRun",
            command_schema_version=1,
            stream_type="run",
            stream_id=str(run),
            owner_user_id="test",
            team_id=None,
            correlation_id=run,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload={},
        )

        class Sink:
            async def receive(self, actual, *, max_active_runs):
                assert actual is command
                assert max_active_runs == 17
                assert journal.get("command", actual.command_id)["body"] == actual.model_dump(
                    mode="json"
                )
                return False

        assert (
            asyncio.run(ObservedSink(Sink(), journal).receive(command, max_active_runs=17)) is False
        )


def test_later_same_key_put_cannot_erase_earlier_uncertain_upload(tmp_path):
    tmp_path.chmod(0o700)
    with RecoveryJournal(tmp_path) as journal:
        run = str(uuid4())
        journal.intent("run", run, {"scope": "user:test"})
        raw = Storage()
        raw.fail = True
        observed = ObservedStorage(raw, journal)
        with pytest.raises(TimeoutError):
            asyncio.run(ActivityObjectStore(observed).put_input(run, {}))
        raw.fail = False
        # Fresh process observer, same durable inventory and same encoded key.
        fresh = ObservedStorage(raw, journal)
        asyncio.run(ActivityObjectStore(fresh).put_input(run, {}))
        with pytest.raises(RuntimeError, match="upload attempt remains uncertain"):
            asyncio.run(fresh.drain())
