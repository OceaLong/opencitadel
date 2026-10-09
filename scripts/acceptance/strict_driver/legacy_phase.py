"""Old/current code phases for an explicitly separate ephemeral migration fixture.

The same file runs with either archived historical API or current API imports.
Never imports pytest or writes final projections/events by SQL.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

EXECUTION_STAGE = "startup"


def require(condition, identity):
    if not condition:
        raise AssertionError(identity)


def hash_original(events):
    # Hash of original identities/versions/hashes. The raw internal event hashes
    # stay inside this process and are never serialized to browser evidence.
    return hashlib.sha256(
        json.dumps(
            [
                (str(event.event_id), event.event_schema_version, event.event_hash)
                for event in events
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def semantic_digest(state):
    fields = (
        "run_id",
        "family",
        "source_entity_type",
        "source_entity_id",
        "owner_user_id",
        "team_id",
        "status",
        "retry_generation",
        "active_activity_ids",
        "settled_activities",
        "requested_activities",
        "activity_generations",
    )
    body = {key: getattr(state, key) for key in fields}
    body["policy_snapshot_digest"] = state.policy_snapshot.snapshot_digest
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def guard(settings, document):
    from sqlalchemy.engine import make_url

    expected = document["legacy"]
    require(settings.env == "test", "legacy_test_only")
    for url in (settings.sqlalchemy_database_uri, settings.sqlalchemy_migration_database_uri):
        parsed = make_url(url)
        require(
            parsed.host == expected["host"] and parsed.database == expected["database"],
            "legacy_database_identity",
        )
        require(parsed.host != "opencitadel-postgres", "never_shared_database")
    require(
        expected["host"].startswith("strict-legacy-")
        and expected["database"].startswith("strict_legacy_"),
        "dedicated_legacy_identity",
    )


async def execute(phase, document, previous):
    global EXECUTION_STAGE
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.domain.execution.aggregate import replay
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate, RunFamily
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.session import Session
    from app.domain.models.user import User
    from app.domain.runtime_policy.snapshot import derive_run_policy_snapshot
    from app.infrastructure.execution.postgres_event_store import PostgresEventStore
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.infrastructure.repositories.db_session_repository import DBSessionRepository
    from app.infrastructure.repositories.db_user_repository import DBUserRepository
    from app.infrastructure.repositories.postgres_runtime_policy_repository import (
        PostgresRuntimePolicyRepository,
    )
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings

    settings = load_deployment_settings()
    guard(settings, document)
    engine = create_async_engine(settings.sqlalchemy_database_uri)
    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        info={
            "database_authorization_signing_secret": settings.database_authorization_signing_secret
        },
    )
    kernel = AuthorizationContext.system("execution-kernel")
    aggregate = RunAggregate()
    try:
        EXECUTION_STAGE = "database_identity"
        async with factory() as db:
            require(
                await db.scalar(text("SELECT current_database()"))
                == document["legacy"]["database"],
                "actual_distinct_database",
            )
        # The kernel role cannot inspect the administrative Alembic table.
        # Read the migration head through the fixture's dedicated migration role.
        migration_engine = create_async_engine(settings.sqlalchemy_migration_database_uri)
        try:
            EXECUTION_STAGE = "migration_head"
            async with migration_engine.connect() as db:
                migration = (
                    await db.scalars(text("SELECT version_num FROM alembic_version"))
                ).one()
        finally:
            await migration_engine.dispose()
        if phase == "old":
            # Construct fixture identity through the actual historical repositories.
            # It is a normal user in a separate DB, never an elevated UI principal.
            user = User(email="strict-legacy@example.invalid", username="strict-legacy")
            scope = OwnerScope.personal(user.id)
            principal = Principal(
                user_id=user.id, global_role=user.global_role, token_version=user.token_version
            )
            source = Session(owner_user_id=user.id, title="Historical entry-point fixture")
            migration_engine = create_async_engine(settings.sqlalchemy_migration_database_uri)
            try:
                EXECUTION_STAGE = "user_seed"
                migration_factory = async_sessionmaker(migration_engine)
                async with migration_factory() as db:
                    # The historical users table forces RLS even for its DDL
                    # owner; this fixture seed is an authorized system write.
                    await configure_session_authorization(
                        db,
                        AuthorizationContext.system("auth"),
                        signing_secret=settings.database_authorization_signing_secret,
                    )
                    await DBUserRepository(db).save(user)
                    await db.commit()
            finally:
                await migration_engine.dispose()
            api_engine = create_async_engine(os.environ["STRICT_LEGACY_API_URI"])
            try:
                EXECUTION_STAGE = "source_session"
                api_factory = async_sessionmaker(
                    api_engine,
                    info={
                        "database_authorization_signing_secret": settings.database_authorization_signing_secret
                    },
                )
                async with api_factory() as db:
                    await configure_session_authorization(
                        db, AuthorizationContext.for_principal(principal, scope=scope)
                    )
                    await DBSessionRepository(db).save(source)
                    await db.commit()
            finally:
                await api_engine.dispose()
            run_id = uuid4()
        else:
            require(
                previous["binding"] == document["binding"]
                and previous["legacy"] == document["legacy"],
                "legacy_phase_binding",
            )
            scope = OwnerScope.personal(previous["user_id"])
            async with factory() as db:
                await configure_session_authorization(db, kernel)
                user = await DBUserRepository(db).get_by_id(scope.user_id)
                require(user is not None and user.is_active, "legacy_current_user")
                principal = Principal(
                    user_id=user.id, global_role=user.global_role, token_version=user.token_version
                )
            source = Session(id=previous["session_id"], owner_user_id=user.id)
            run_id = UUID(previous["run_id"])
            # The historical projector had no view journal. Rebuild old
            # formal observations before writing current events so the
            # immutable view order remains old-to-new.
            EXECUTION_STAGE = "initial_formal_rebuild"
            initial_rebuild = await PostgresFormalProjector(
                session_factory=factory, authorization=kernel
            ).rebuild(scope)
            require(
                initial_rebuild.processed == previous["event_count"],
                "initial_formal_rebuild_complete",
            )
        handler = SqlAlchemyExecutionOrchestrator(
            session_factory=factory, aggregates={"run": aggregate}, authorization=kernel
        )

        async def send(kind, payload=None, version=1):
            result = await handler.handle(
                CommandEnvelope(
                    command_id=uuid4(),
                    command_type=kind,
                    command_schema_version=version,
                    stream_type="run",
                    stream_id=str(run_id),
                    owner_user_id=scope.user_id,
                    team_id=None,
                    correlation_id=run_id,
                    causation_id=None,
                    issued_at=datetime.now(UTC),
                    payload=payload or {},
                )
            )
            require(result.status == "accepted", kind + "_accepted")

        if phase == "old":
            EXECUTION_STAGE = "policy_load"
            policy = await PostgresRuntimePolicyRepository(
                session_factory=factory,
                authorization=AuthorizationContext.system("runtime-policy-reader"),
            ).load_active_pair()
            snapshot = derive_run_policy_snapshot(policy.execution, RunFamily.AGENT)
            EXECUTION_STAGE = "run_commands"
            await send(
                "CreateRun",
                {
                    "family": "agent",
                    "source_entity_type": "session",
                    "source_entity_id": source.id,
                    "semantic_payload": {},
                    "public_input": {"message": "legacy semantic input"},
                    "policy_snapshot": snapshot.model_dump(mode="json"),
                },
            )
            await send("StartRun")
        EXECUTION_STAGE = "activity_commands"
        identity = uuid4()
        await send(
            "RequestActivity",
            {
                "activity_id": str(identity),
                "activity_type": "tool.call",
                "input_digest": "legacy-contract",
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                **({"parent_activity_id": None, "invocation_id": None} if phase != "old" else {}),
            },
            1 if phase == "old" else 2,
        )
        await send(
            "MarkActivityCallStarted",
            {
                "activity_id": str(identity),
                "generation": 0,
                **({"claim_generation": 1} if phase != "old" else {}),
            },
            1 if phase == "old" else 2,
        )
        await send(
            "CompleteActivity",
            {
                "activity_id": str(identity),
                "generation": 0,
                **({"claim_generation": 1} if phase != "old" else {}),
            },
            1 if phase == "old" else 2,
        )
        if phase != "old":
            await send("CompleteRun")
        EXECUTION_STAGE = "formal_projection"
        projector = PostgresFormalProjector(session_factory=factory, authorization=kernel)
        projected = await projector.run_once(scope, limit=100, notify=False)
        require(projected.processed > 0, "genuine_projector_checkpoint")
        EXECUTION_STAGE = "stream_replay"
        async with factory() as db:
            await configure_session_authorization(db, kernel)
            raw = await PostgresEventStore(db).load_stream("run", str(run_id))
            upcast = await PostgresEventStore(
                db, event_registries={"run": aggregate.event_registry}
            ).load_stream("run", str(run_id))
        state = replay(aggregate, upcast).state
        authorized = AuthorizationContext.for_principal(principal, scope=scope)
        EXECUTION_STAGE = "source_governance"
        governance = await PostgresRunProjection(
            session_factory=factory, authorization=authorized
        ).source_governance(
            source_entity_type="session", source_entity_id=source.id, owner_scope=scope
        )
        require(
            governance["chain"]["verified"] is True and governance["chain"]["checked_runs"] == 1,
            "old_entrypoint_hash_verified",
        )
        report = {
            "binding": document["binding"],
            "legacy": document["legacy"],
            "phase": phase,
            "user_id": scope.user_id,
            "session_id": source.id,
            "run_id": str(run_id),
            "migration": migration,
            "event_count": len(raw),
            "raw_stream_digest": hash_original(raw),
            "projected_position": projected.last_position,
            "status": state.status.value,
            "semantic_digest": semantic_digest(state),
            "governance": governance,
        }
        if phase != "old":
            from app.application.services.execution_view_service import ExecutionViewService
            from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView

            old_count = previous["event_count"]
            require(
                hash_original(raw[:old_count]) == previous["raw_stream_digest"],
                "original_hashes_unchanged",
            )
            require(
                all(a.event_hash == b.event_hash for a, b in zip(raw, upcast, strict=True)),
                "upcast_preserves_original_hash",
            )
            old_result = replay(aggregate, upcast[:old_count]).state
            require(
                semantic_digest(old_result) == previous["semantic_digest"],
                "old_aggregate_semantics_preserved",
            )
            require(
                any(
                    a.event_schema_version < b.event_schema_version
                    for a, b in zip(raw[:old_count], upcast[:old_count], strict=True)
                ),
                "actual_upcast_exercised",
            )
            require(
                any(event.event_schema_version == 2 for event in raw[old_count:]),
                "actual_new_events_coexist",
            )
            require(
                all(
                    event.public_payload.get("parent_activity_id") is None
                    and event.public_payload.get("invocation_id") is None
                    for event in upcast[:old_count]
                    if event.event_type == "ActivityRequested"
                ),
                "legacy_relationships_unknown",
            )
            require(
                all(
                    event.public_payload.get("claim_generation") is None
                    for event in upcast[:old_count]
                    if event.event_type == "ActivityCallStarted"
                ),
                "legacy_claim_unknown",
            )
            port = PostgresExecutionView(session_factory=factory, authorization=authorized)
            views = ExecutionViewService(
                port, cursor_secret=hashlib.sha256(settings.api_key_secret.encode()).digest()
            )
            EXECUTION_STAGE = "view_before_shadow"
            before = await views.get_view(scope, run_id)
            require(
                any(
                    item.reason == "pre_journal_progress_unavailable"
                    for item in before.run.completeness.missing_intervals
                ),
                "genuine_pre_journal_gap",
            )
            require(
                before.run.completeness.state == "partial" and bool(before.steps),
                "available_new_segment_not_claimed_complete",
            )
            EXECUTION_STAGE = "shadow_rebuild"
            shadow = await port.rebuild_scope_shadow(scope)
            EXECUTION_STAGE = "view_after_shadow"
            after = await views.get_view(scope, run_id)
            require(
                shadow.activated and after.run.completeness == before.run.completeness,
                "gap_preserved_through_shadow_activation",
            )
            require(after.run.status.value == "completed", "available_new_terminal_fact")
            print(
                json.dumps(
                    {
                        "kind": "checkpoint",
                        "body": {
                            **report,
                            "before": before.model_dump(mode="json"),
                            "after": after.model_dump(mode="json"),
                        },
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            # Rebuild real disposable formal aggregates, preserving immutable
            # event/observation facts. Compare the entire current replay state.
            from app.domain.execution.serialization import canonical_state_hash

            EXECUTION_STAGE = "formal_rebuild"
            rebuilt = await projector.rebuild(scope)
            require(rebuilt.processed == len(raw), "complete_formal_rebuild")
            async with factory() as db:
                await configure_session_authorization(db, kernel)
                stored = await db.scalar(
                    text("SELECT state FROM execution_run_projection WHERE run_id=:run"),
                    {"run": run_id},
                )
                require(
                    canonical_state_hash(stored) == canonical_state_hash(state),
                    "formal_rebuild_matches_aggregate_replay",
                )
                require(
                    hash_original(await PostgresEventStore(db).load_stream("run", str(run_id)))
                    == hash_original(raw),
                    "formal_rebuild_original_hashes_unchanged",
                )
            rebuilt_governance = await PostgresRunProjection(
                session_factory=factory, authorization=authorized
            ).source_governance(
                source_entity_type="session", source_entity_id=source.id, owner_scope=scope
            )
            require(
                rebuilt_governance["chain"]["verified"] is True, "rebuilt_old_entrypoint_verified"
            )
            EXECUTION_STAGE = "view_after_formal_rebuild"
            post_rebuild = await views.get_view(scope, run_id)
            require(
                post_rebuild.run.status == after.run.status, "formal_rebuild_view_status_preserved"
            )
            require(
                post_rebuild.run.completeness == after.run.completeness,
                "formal_rebuild_view_completeness_preserved",
            )
            require(
                [step.model_dump(mode="json") for step in post_rebuild.steps]
                == [step.model_dump(mode="json") for step in after.steps],
                "formal_rebuild_view_ordered_steps_preserved",
            )
            report["governance"] = rebuilt_governance
            report.update(
                before=before.model_dump(mode="json"),
                after=after.model_dump(mode="json"),
                old_hashes_unchanged=True,
                old_replay_equal=True,
                legacy_missing_fields_unknown=True,
                formal_rebuild_equal=True,
                upcast_hashes_unchanged=True,
                new_events_coexist=True,
                shadow_activated=True,
            )
        return report
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("old", "current"), required=True)
    args = parser.parse_args()
    stage = "input"
    try:
        document = json.loads(Path("/legacy-input.json").read_text())
        from core.config import load_deployment_settings

        settings = load_deployment_settings()
        guard(settings, document)  # Refuse main DB BEFORE migrations or any connection.
        stage = "migration"
        from app.migrate import main as migrate

        with contextlib.redirect_stdout(sys.stderr):
            migrate()
        previous = (
            json.loads(Path("/legacy-before.json").read_text()) if args.phase == "current" else None
        )
        stage = "execution"
        report = asyncio.run(execute(args.phase, document, previous))
        print(json.dumps({"kind": "report", "body": report}, sort_keys=True), flush=True)
    except BaseException as exc:
        original = getattr(exc, "orig", None)
        failure = {"stage": stage, "type": type(exc).__name__}
        sqlstate = getattr(original, "sqlstate", None)
        if isinstance(sqlstate, str) and len(sqlstate) == 5:
            failure["sqlstate"] = sqlstate
        match = re.search(
            r"(?:permission denied for|must be owner of) "
            r"([a-zA-Z_]+) ([a-zA-Z_][a-zA-Z0-9_.]*)",
            str(original),
            re.IGNORECASE,
        )
        if match:
            failure["denied_object"] = {"kind": match.group(1), "name": match.group(2)}
        statement = getattr(exc, "statement", None)
        if isinstance(statement, str):
            target = re.search(
                r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
                statement,
                re.IGNORECASE,
            )
            if target:
                failure["sql_target"] = target.group(1)
        if stage == "execution":
            failure["stage"] = EXECUTION_STAGE
        if type(exc).__name__ == "ViewRevisionExpired":
            reason = str(exc)
            if reason in {
                "run state is not reconstructable at this boundary",
                "view generation retired; reload current view",
                "event generation retired",
                "event generation changed during read",
                "playback source unavailable",
                "no replayable observation at this boundary",
            }:
                failure["reason"] = reason
        if isinstance(exc, AssertionError) and str(exc).replace("_", "").isalnum():
            failure["assertion"] = str(exc)
        print(json.dumps({"kind": "failure", "body": failure}, sort_keys=True), flush=True)
        raise


if __name__ == "__main__":
    main()
