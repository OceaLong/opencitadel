"""Read-only parent ordering, upload containment and cancellation tests."""

import asyncio
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scripts.execution_capacity.batch_facts import BatchStorage
from scripts.execution_capacity.observers import RecoveryJournal


def test_concurrent_uploads_never_exceed_five_started_sdk_calls(tmp_path):
    async def scenario():
        root = tmp_path / "private"
        root.mkdir(mode=0o700)
        with RecoveryJournal(root) as journal:
            run = uuid4()
            journal.intent("run", run, {"scope": "user:owned"})
            gate = asyncio.Event()
            active = 0
            high = 0
            bodies = {}

            async def own(key):
                assert key.startswith(f"execution/inputs/{run}/")

            class Storage:
                async def put_bytes(self, key, data):
                    nonlocal active, high
                    assert journal.get("object", key) is not None
                    active += 1
                    high = max(high, active)
                    await gate.wait()
                    bodies[key] = data
                    active -= 1

                async def get_bytes(self, key):
                    return bodies[key]

            storage = BatchStorage(
                Storage(), journal, SimpleNamespace(own_object_parent=own), queue_limit=8
            )
            uploads = []
            for i in range(8):
                body = str(i).encode()
                key = f"execution/inputs/{run}/{sha256(body).hexdigest()}.json"
                uploads.append(asyncio.create_task(storage.put_bytes(key, body)))
            for _ in range(20):
                await asyncio.sleep(0)
            assert active == high == 5
            uploads[-1].cancel()  # waiting: never reached SDK, no uncertain upload
            await asyncio.gather(uploads[-1], return_exceptions=True)
            gate.set()
            await asyncio.gather(*uploads[:-1])
            await storage.drain()
            assert high == 5
            assert len(list(journal.records("upload"))) == 7

    asyncio.run(scenario())


def test_foreign_parent_fails_before_physical_put(tmp_path):
    async def scenario():
        root = tmp_path / "private"
        root.mkdir(mode=0o700)
        with RecoveryJournal(root) as journal:

            async def refuse(key):
                raise ValueError("uncommitted owned parent")

            class Storage:
                async def put_bytes(self, *args):
                    pytest.fail("physical write preceded committed authority")

            storage = BatchStorage(
                Storage(), journal, SimpleNamespace(own_object_parent=refuse), queue_limit=8
            )
            with pytest.raises(ValueError, match="uncommitted"):
                await storage.put_bytes("any", b"private")
            assert not list(journal.records("upload"))

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [True, False])
def test_actual_subject_parent_query_precedes_object_registry_and_sdk(tmp_path, committed):
    from contextlib import asynccontextmanager

    from scripts.execution_capacity.batch_facts import BatchFacts

    from app.domain.models.scope import OwnerScope

    async def scenario():
        root = tmp_path / "private"
        root.mkdir(mode=0o700)
        with RecoveryJournal(root) as journal:
            batch, run = uuid4(), uuid4()
            journal.intent("batch", batch, {"scope": "user:owned"})
            facts = BatchFacts(
                None, None, journal, OwnerScope.personal("owned"), None, batch_id=batch
            )
            seen = []

            class DB:
                async def execute(self, sql, parameters):
                    seen.append(str(sql))
                    assert parameters["batch"] == batch
                    assert parameters["run"] == run
                    rows = (
                        [
                            {
                                "result_id": uuid4(),
                                "attempt": 0,
                                "intent": {"original": "admission"} if committed else None,
                                "case_revision_id": uuid4(),
                                "config_version_id": uuid4(),
                                "repetition": 0,
                            }
                        ]
                        if "evaluation_batch_attempts" in str(sql)
                        else []
                    )
                    return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

            @asynccontextmanager
            async def session():
                yield DB()

            facts.session = session
            body = b"actual input"
            key = f"execution/inputs/{run}/{sha256(body).hexdigest()}.json"

            class Storage:
                async def put_bytes(self, k, data):
                    assert len(seen) == 2
                    assert journal.parent("run", run)["parent"]["intent"] == {
                        "original": "admission"
                    }
                    assert journal.parent("object", k)["sha256"] == sha256(data).hexdigest()

                async def get_bytes(self, k):
                    return body

            storage = BatchStorage(Storage(), journal, facts, queue_limit=8)
            if committed:
                await storage.put_bytes(key, body)
                await storage.drain()
            else:
                with pytest.raises(ValueError, match="not committed"):
                    await storage.put_bytes(key, body)
                assert not list(journal.records("upload"))

    asyncio.run(scenario())


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [True, False])
async def test_actual_claim_retains_original_before_ownership_predicate(tmp_path, owned):
    from contextlib import asynccontextmanager

    from scripts.execution_capacity.batch_facts import BatchFacts
    from scripts.execution_capacity.evidence_owner import EvidenceOwner

    from app.domain.models.scope import OwnerScope

    owner = EvidenceOwner()
    run, activity = uuid4(), uuid4()
    raw = {
        "aggregate_id": str(run),
        "owner_user_id": "owned" if owned else "foreign",
        "team_id": None,
        "request_generation": 2,
        "claim_generation": 3,
        "status": "claimed",
    }
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    with RecoveryJournal(root) as journal:
        facts = object.__new__(BatchFacts)
        facts.evidence, facts.scope, facts.scope_key, facts.journal = (
            owner,
            OwnerScope.personal("owned"),
            "user:owned",
            journal,
        )

        class DB:
            async def execute(self, statement, params):
                assert params == {"id": activity}
                return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: raw))

        @asynccontextmanager
        async def session():
            yield DB()

        async def own_run(value):
            assert value == str(run)
            assert owner.originals["batch-source"][0]["claim"] == raw

        facts.session, facts.own_run = session, own_run
        key = f"execution/results/{activity}/{'a' * 64}.json"
        if owned:
            await facts.own_object_parent(key)
            assert journal.get("batch_claim", f"{activity}:3")["body"]["generation"] == 2
        else:
            with pytest.raises(ValueError, match="active claim"):
                await facts.own_object_parent(key)
        assert owner.originals["batch-source"][0]["claim"] == raw
