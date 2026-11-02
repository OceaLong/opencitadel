# ruff: noqa: F401,F811 -- isolated PostgreSQL fixture graph
"""A03: a new Session Run sees the original unknown effect after cancellation."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.execution.effect_reconciler import EffectReconciler
from app.domain.execution.run import RunFamily
from app.infrastructure.execution.postgres_context_sources import PostgresContextSources
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from tests.app.infrastructure.repositories.test_context_physical_dispatch import (
    budget_binding_fixture,
    compact_runtime,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    object_sessions,
)
from tests.app.interfaces.endpoints.test_effect_api_postgres import effect_http

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize(
    "effect_http",
    [
        {
            "source_entity_type": "session",
            "source_entity_id": "continued-unknown-session",
            "private_input": {
                "message": "Write ORD83 once; never repeat an unresolved operation.",
                "mode": "agent",
            },
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    "compact_runtime",
    [
        {
            "ordinary": True,
            "ready": True,
            "real_preparation": True,
            "iterations": 1,
            "session_messages": 1,
            "context_policy": {"selected_sources": 8, "recent_units": 1},
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    ("history_turns", "resolve_after_history"),
    [(0, False), (12, False), (12, True)],
    ids=["direct", "after-compaction", "query-applied-after-compaction"],
)
async def test_cancel_then_continue_preserves_unknown_identity_at_actual_model_send(
    effect_http,
    compact_runtime,
    history_turns,
    resolve_after_history,
):
    prior, r = effect_http, compact_runtime
    assert r.scope == prior.scope
    await prior.send("CancelRun", {"reason": "continue in the next Session turn"})
    # Both fixtures represent the same owned immutable object service; copy the
    # original bytes, never edit a formal reference or reseal an event hash.
    for key, body in prior.stored_objects.items():
        await r.objects._storage.put_bytes(key, body)

    async def turn(message):
        run_id = uuid4()
        async with r.api_uow() as work:
            cut = await PostgresContextSources(work.db_session, signing_secret="owned-cut").freeze(
                r.scope, "continued-unknown-session", request_id=uuid4()
            )
            await r.admission.admit(
                family=RunFamily.ASK,
                source_entity_type="session",
                source_entity_id="continued-unknown-session",
                owner_scope=r.scope,
                run_id=run_id,
                private_input={
                    "message": message,
                    "mode": "ask",
                    "model_id": "e02-model",
                    "conversation": [],
                    "_context_boundary": cut.model_dump(mode="json"),
                },
                public_input={},
                command_sink=work.execution_commands,
                inference_read_context=work,
            )
            await work.commit()
        for _ in range(100):
            inbox = await r.kernel.run_inbox_once(limit=10)
            assert not inbox.rejected
            await r.kernel.run_projector_once(r.scope, limit=100)
            decisions = await r.kernel.run_decisions_once(limit=10)
            assert not decisions.errors
            activities = await r.kernel.run_activities_once(limit=1)
            assert not activities.failed
            assert not activities.unknown
            if not inbox.accepted and not decisions.submitted and not activities.claimed:
                break
        return run_id

    for ordinal in range(history_turns):
        await turn(
            f"Unrelated progress {ordinal}: "
            + "Routine update, no external operation requested. " * 40
        )
    if resolve_after_history:
        # Cancellation already queued automatic recovery. A deployment without
        # this optional resolver leaves the operation unknown; a later worker
        # with the resolver can consume the user's explicit query.
        await EffectReconciler(service=prior.producer).process_pending()
        path = f"{prior.base}/{prior.spec.operation_id}"
        response = await prior.http.post(
            path + "/queries",
            json={"expected_revision": 3},
            headers={"Idempotency-Key": "continued-operation-query"},
        )
        assert response.status_code == 202, response.text
        queried = []

        async def resolve(spec, scope):
            assert spec == prior.spec
            assert scope == prior.scope
            queried.append(spec.operation_id)
            return {
                "state": "applied",
                "evidence": {"operation_id": str(spec.operation_id), "receipt": "ORD83-once"},
                "result": {"receipt": "ORD83-once"},
            }

        # A new recovery worker consumes the durable HTTP request; it can query
        # the receipt but has no external write implementation.
        worker = EffectReconciler(
            service=prior.producer, resolvers={"controlled-provider": resolve}
        )
        # The cancelled Run may also have kernel-owned recovery work queued.
        # The queried operation and this HTTP request must still settle once.
        assert await worker.process_pending() >= 1
        assert await worker.process_pending() == 0
        assert queried == [prior.spec.operation_id]
        request_id = response.json()["data"]["request_id"]
        request_status = await prior.http.get(path + "/requests/" + request_id)
        assert request_status.status_code == 200, request_status.text
        assert request_status.json()["data"]["status"] == "completed", request_status.text
        detail = await prior.http.get(path)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["state"] == "applied"
        for key, body in prior.stored_objects.items():
            await r.objects._storage.put_bytes(key, body)
    run_id = await turn(
        "Continue ORD83. Report the unresolved operation before doing anything else."
    )
    if history_turns:
        assert r.responses.summary, "the continuation must traverse actual compaction"
    wires = [
        wire
        for wire in r.responses.main
        if any("Continue ORD83." in message.get("content", "") for message in wire["messages"])
    ]
    assert len(wires) == 1
    evidence = "\n".join(message.get("content", "") for message in wires[0]["messages"])
    assert str(prior.spec.operation_id) in evidence
    assert str(prior.spec.invocation_id) in evidence
    assert prior.spec.contract_digest in evidence
    expected_state = "applied" if resolve_after_history else "unknown"
    assert f'"state":"{expected_state}"' in evidence
    assert not wires[0].get("tools")
    async with r.uow(r.auth) as work:
        current = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                run_id, owner_scope=(r.scope.user_id, None)
            )
        ).replay.state
        original = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                prior.spec.run_id, owner_scope=(r.scope.user_id, None)
            )
        ).replay.state
        assert current.status.value == "completed"
        assert original.status.value == "cancelled"
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_events WHERE event_type='EffectOperationPrepared'"
                )
            )
            == 1
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_events WHERE event_type='EffectDispatchStarted'"
                )
            )
            == 1
        )
