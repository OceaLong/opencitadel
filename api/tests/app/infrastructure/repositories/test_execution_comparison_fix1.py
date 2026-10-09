"""A02 adversarial PostgreSQL gates. Definitions/collection only while infra is deferred."""

# ruff: noqa: F401,F811
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.ports.execution_comparison import ComparisonRequest
from app.application.services.comparison_artifact_reader import ComparisonArtifactReader
from app.application.services.comparison_diff_worker import ComparisonDiffWorker
from app.domain.external.object_storage import BoundedObjectBytes
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.repositories.db_comparison_diff_jobs import DBComparisonDiffJobs
from app.infrastructure.repositories.db_execution_comparison_repository import (
    DBExecutionComparisonRepository,
    comparison_owner_validator,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    execution_kernel_database_uri,
)
from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
    budget_binding_fixture,
    capture,
    comparison_request,
    configurations,
    datasets,
    fixed_artifact_comparison,
    fresh_f07_database,
    isolated_database,
    make_run,
    repository,
    seed_model_score,
    write,
)

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def kernel_jobs(repo, isolated_database):
    original, _ = isolated_database
    engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(database=original.url.database),
        pool_size=1,
        max_overflow=0,
    )
    try:
        factory = authenticated_session_factory(engine, signing_secret=repo.secret)
        yield (
            DBComparisonDiffJobs(
                DBExecutionComparisonRepository(factory, signing_secret=repo.secret)
            ),
            engine,
        )
    finally:
        await engine.dispose()


async def team_callers(datasets):
    service, _, principal, *_ = datasets
    team, other = str(uuid4()), str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO users(id,email,username) VALUES(:id,:email,:id)"),
            {"id": other, "email": other + "@test.invalid"},
        )
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
        for caller in (principal.user_id, other):
            await db.execute(
                text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
                {"team": team, "user": caller},
            )
        await db.commit()
    first = principal.model_copy(update={"team_roles": {team: "member"}})
    second = Principal(user_id=other, team_roles={team: "member"})
    return (
        service,
        OwnerScope.team(first.user_id, team),
        first,
        OwnerScope.team(other, team),
        second,
    )


async def test_workspace_shared_comparison_but_private_cursor_receipt_and_scope(datasets):
    service, scope, first, other_scope, second = await team_callers(datasets)
    runs = [await make_run(scope) for _ in range(2)]
    request = comparison_request(
        {"request_id": "same-client-id", "mode": "explicit", "run_ids": runs}
    )
    repo = repository(service)
    identity, revision = await repo.materialize(scope, first, request)
    # Resolve the actual retained owner server-side, never a client-invented UUID.
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.for_principal(first, scope=scope), signing_secret=repo.secret
        )
        capture_id = await db.scalar(
            text(
                "SELECT id FROM comparison_revisions WHERE comparison_id=:id AND revision=:revision"
            ),
            {"id": identity, "revision": revision},
        )
    assert capture_id is not None
    async with repo.transactions.transaction(scope, first) as db:
        assert await comparison_owner_validator(first, signing_secret=repo.secret)(
            db, scope, str(capture_id)
        )
    # Workspace sharing also applies to a real revision's owner validation.
    async with repo.transactions.transaction(other_scope, second) as db:
        assert await comparison_owner_validator(second, signing_secret=repo.secret)(
            db, other_scope, str(capture_id)
        )
    with pytest.raises(DBAPIError, match="comparison_not_found"):
        async with repo.transactions.transaction(OwnerScope.personal(second.user_id), second) as db:
            await comparison_owner_validator(second, signing_secret=repo.secret)(
                db, OwnerScope.personal(second.user_id), str(capture_id)
            )
    owner_page = await repo.read(scope, first, identity, revision, limit=1)
    shared_page = await repo.read(other_scope, second, identity, revision, limit=1)
    assert shared_page.body["member_count"] == 2
    with pytest.raises(ValueError, match="cursor"):
        await repo.read(
            other_scope, second, identity, revision, cursor=owner_page.body["next_cursor"]
        )
    other_identity, _ = await repo.materialize(other_scope, second, request)
    assert other_identity != identity  # caller-private receipt, despite same command id
    with pytest.raises((ValueError, PermissionError)):
        await repo.read(OwnerScope.personal(second.user_id), second, identity, revision)
    async with repo.transactions.transaction(OwnerScope.personal(second.user_id), second) as db:
        with pytest.raises(DBAPIError):
            await comparison_owner_validator(second, signing_secret=repo.secret)(
                db, OwnerScope.personal(second.user_id), str(uuid4())
            )
    async with execution_admin_session() as db:
        await db.execute(
            text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
            {"team": scope.team_id, "user": second.user_id},
        )
        await db.commit()
    with pytest.raises(PermissionError):
        await repo.read(other_scope, second, identity, revision)
    with pytest.raises(DBAPIError, match="analysis_authorization"):
        async with repo.transactions.transaction(other_scope, second) as db:
            await comparison_owner_validator(second, signing_secret=repo.secret)(
                db, other_scope, str(capture_id)
            )


async def test_create_refresh_receipts_replay_lost_responses_and_reject_changed_target(datasets):
    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    repo = repository(service)
    payload = {"request_id": "create-lost-response", "mode": "explicit", "run_ids": [run]}
    first = await repo.materialize(scope, principal, comparison_request(payload))
    assert await repo.materialize(scope, principal, comparison_request(payload)) == first
    with pytest.raises(ValueError, match="request_conflict"):
        await repo.materialize(
            scope, principal, comparison_request({**payload, "detail_run_ids": [run]})
        )
    refresh = comparison_request({**payload, "request_id": "refresh-lost-response"})
    newer = await repo.materialize(
        scope, principal, refresh, comparison_id=first[0], expected_revision=1
    )
    assert (
        await repo.materialize(
            scope, principal, refresh, comparison_id=first[0], expected_revision=1
        )
        == newer
    )
    with pytest.raises(ValueError, match="request_conflict"):
        await repo.materialize(
            scope, principal, refresh, comparison_id=first[0], expected_revision=2
        )
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    with pytest.raises(PermissionError):
        await repo.materialize(scope, principal, comparison_request(payload))


async def aligned(datasets):
    service, scope, principal, *_ = datasets
    runs = [await make_run(scope) for _ in range(2)]
    for run in runs:
        await write(
            run,
            scope,
            2,
            {"kind": "tool", "status": "completed", "activity_id": str(uuid4())},
            kind="step",
            identity="step",
        )
    repo = repository(service)
    identity, revision = await repo.materialize(
        scope,
        principal,
        comparison_request({"mode": "explicit", "run_ids": runs, "detail_run_ids": runs}),
    )
    edit = {
        "left_run_id": runs[0],
        "right_run_id": runs[1],
        "left_step_id": "step",
        "right_step_id": "step",
        "action": "confirm",
    }
    return repo, scope, principal, identity, revision, edit


async def test_alignment_receipt_replays_after_cas_and_conflicts_on_changed_edits(datasets):
    repo, scope, principal, identity, revision, edit = await aligned(datasets)

    async def align(edits):
        return await repo.align(
            scope,
            principal,
            identity,
            revision,
            expected_revision=0,
            edits=edits,
            request_id="align-lost-response",
        )

    assert await align([edit]) == await align([edit]) == 1
    with pytest.raises(ValueError, match="request_conflict"):
        await align([{**edit, "action": "unpair"}])


@pytest.mark.parametrize("operation", ["create_same_receipt", "refresh", "align"])
async def test_simultaneous_mutations_have_one_effect_or_cas_winner(datasets, operation):
    repo, scope, principal, identity, revision, edit = await aligned(datasets)
    request = comparison_request({"mode": "all_matching"})

    async def command(number):
        if operation == "create_same_receipt":
            return await repo.materialize(scope, principal, request)
        if operation == "refresh":
            return await repo.materialize(
                scope,
                principal,
                replace(request, request_id=str(uuid4())),
                comparison_id=identity,
                expected_revision=revision,
            )
        return await repo.align(
            scope,
            principal,
            identity,
            revision,
            expected_revision=0,
            edits=[edit],
            request_id=str(number),
        )

    result = await asyncio.gather(command(0), command(1), return_exceptions=True)
    if operation == "create_same_receipt":
        assert result[0] == result[1]
        assert not isinstance(result[0], Exception)
    else:
        assert sum(not isinstance(value, Exception) for value in result) == 1
        assert (
            sum(isinstance(value, ValueError) and "conflict" in str(value) for value in result) == 1
        )


async def test_diff_receipts_scope_private_jobs_and_runtime_kernel_grants(
    datasets, isolated_database
):
    repo, scope, principal, identity, revision, selection = await fixed_artifact_comparison(
        datasets
    )
    jobs = DBComparisonDiffJobs(repo)
    queued = await jobs.enqueue(
        scope, principal, identity, revision, selection, request_id="diff-lost"
    )
    assert (
        await jobs.enqueue(scope, principal, identity, revision, selection, request_id="diff-lost")
        == queued
    )
    with pytest.raises(ValueError, match="request_conflict"):
        await jobs.enqueue(
            scope,
            principal,
            identity,
            revision,
            {**selection, "format": "json"},
            request_id="diff-lost",
        )
    async with repo.transactions.transaction(scope, principal) as db:
        assert not await db.scalar(
            text(
                "SELECT has_function_privilege(current_user,'opencitadel_comparison_diff_claim(uuid,uuid)','EXECUTE')"
            )
        )
        for table in ("comparison_receipts", "comparison_diff_jobs", "comparison_members"):
            assert not await db.scalar(
                text(
                    "SELECT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE')"
                ),
                {"table": table},
            )
    async with kernel_jobs(repo, isolated_database) as (kernel, _engine):
        claimed = await kernel.claim()
        assert claimed["id"] == queued["job_id"]
        async with kernel.comparisons.session_factory() as db:
            await configure_session_authorization(
                db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
            )
            assert await db.scalar(
                text(
                    "SELECT has_function_privilege(current_user,'opencitadel_comparison_diff_claim(uuid,uuid)','EXECUTE')"
                )
            )
            assert not await db.scalar(
                text(
                    "SELECT has_table_privilege(current_user,'comparison_diff_jobs','SELECT,UPDATE')"
                )
            )


async def test_multiworker_claim_and_real_worker_restart_use_runtime_role_pool_one(
    datasets, isolated_database
):
    repo, scope, principal, identity, revision, selection = await fixed_artifact_comparison(
        datasets
    )
    async with execution_admin_session() as db:
        storage_key = await db.scalar(
            text("SELECT version_refs->>0 FROM artifacts WHERE id=:id"),
            {"id": selection["left"]["artifact_id"]},
        )
    queued = await DBComparisonDiffJobs(repo).enqueue(
        scope, principal, identity, revision, selection, request_id="restart"
    )
    async with (
        kernel_jobs(repo, isolated_database) as (one, _),
        kernel_jobs(repo, isolated_database) as (two, _),
    ):
        claims = await asyncio.gather(one.claim(), two.claim())
        assert sum(value is not None for value in claims) == 1
        claimed = next(value for value in claims if value is not None)
        assert claimed["id"] == queued["job_id"]
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
        )
        await db.execute(
            text(
                "UPDATE comparison_diff_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": claimed["id"]},
        )
        await db.commit()
    async with kernel_jobs(repo, isolated_database) as (restarted, _):

        class Objects:
            async def get_bounded_bytes(self, key, limit):
                # Reenter the SAME size-one runtime pool during source I/O. A held
                # claim/reader transaction deadlocks this real worker test.
                async with restarted.comparisons.session_factory() as db:
                    assert await db.scalar(text("SELECT 1")) == 1
                assert key == storage_key
                return BoundedObjectBytes(b"fixed", False)

        class Compute:
            async def compute(self, left, right, kind):
                assert left == right == b"fixed"
                return {
                    "complete": True,
                    "content_changed": False,
                    "reason": None,
                    "content": "",
                    "operations": [],
                }

        worker = ComparisonDiffWorker(
            restarted,
            lambda *_: ComparisonArtifactReader(restarted.comparisons, Objects()),
            Compute(),
        )
        assert await asyncio.wait_for(worker.process_pending(), 10) == 1
    page = await DBComparisonDiffJobs(repo).page(scope, principal, queued["job_id"])
    assert page["status"] == "complete"
    assert page["result"]["content_changed"] is False


async def test_receipt_and_materialization_rollback_if_pin_acquisition_fails(datasets, monkeypatch):
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    request = comparison_request(
        {
            "request_id": "rollback-command",
            "mode": "explicit",
            "run_ids": [run],
            "detail_run_ids": [run],
        }
    )
    repo = repository(service)
    original = DBResourcePinRepository.acquire

    async def failure(*args, **kwargs):
        raise OSError("pin acquisition failed")

    monkeypatch.setattr(DBResourcePinRepository, "acquire", failure)
    with pytest.raises(OSError, match="pin acquisition"):
        await repo.materialize(scope, principal, request)
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=repo.secret,
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM comparison_receipts WHERE caller_id=:id AND request_id='rollback-command'"
                ),
                {"id": principal.user_id},
            )
            == 0
        )
    monkeypatch.setattr(DBResourcePinRepository, "acquire", original)
    identity, revision = await repo.materialize(scope, principal, request)
    assert await repo.materialize(scope, principal, request) == (identity, revision)


async def test_current_revocation_between_materialization_and_publication_never_releases_old_body(
    datasets,
):
    from app.application.services.execution_comparison_service import ExecutionComparisonService

    repo, scope, principal, identity, revision, selection = await fixed_artifact_comparison(
        datasets
    )
    operation = repo._operation
    ready, resume = asyncio.Event(), asyncio.Event()

    async def intercepted(db, owner, caller, function, action, **payload):
        if function == "control" and action == "publish":
            ready.set()
            await resume.wait()
        return await operation(db, owner, caller, function, action, **payload)

    repo._operation = intercepted

    async def revoke():
        await asyncio.wait_for(ready.wait(), 60)
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "UPDATE artifact_version_provenance SET availability='unavailable' WHERE artifact_id=:id"
                ),
                {"id": selection["left"]["artifact_id"]},
            )
            await db.commit()
        resume.set()

    removal = asyncio.create_task(revoke())
    try:
        body = await asyncio.wait_for(
            ExecutionComparisonService(repo).refresh(
                scope,
                principal,
                identity,
                {
                    "request_id": "revoked-during-refresh",
                    "expected_revision": revision,
                    "mode": "explicit",
                    "run_ids": [selection["left"]["run_id"]],
                    "detail_run_ids": [selection["left"]["run_id"]],
                },
            ),
            90,
        )
    except (PermissionError, ValueError) as error:
        assert ready.is_set(), f"publish barrier not reached: {error}"  # noqa: PT017
        assert "unavailable" in str(error) or "coverage_changed" in str(error)  # noqa: PT017
    else:
        assert body["member_count"] == 0
        assert selection["left"]["artifact_id"] not in str(body)
    finally:
        resume.set()
        if ready.is_set():
            await removal
        else:
            removal.cancel()
            await asyncio.gather(removal, return_exceptions=True)


async def test_worker_revocation_during_real_pool_io_does_not_publish(datasets, isolated_database):
    repo, scope, principal, identity, revision, selection = await fixed_artifact_comparison(
        datasets
    )
    queued = await DBComparisonDiffJobs(repo).enqueue(
        scope, principal, identity, revision, selection, request_id="revoke-io"
    )
    async with kernel_jobs(repo, isolated_database) as (kernel, _):

        class Objects:
            async def get_bounded_bytes(self, key, limit):
                async with execution_admin_session() as db:
                    await db.execute(
                        text(
                            "UPDATE artifact_version_provenance SET availability='unavailable' WHERE artifact_id=:id"
                        ),
                        {"id": selection["left"]["artifact_id"]},
                    )
                    await db.commit()
                return BoundedObjectBytes(b"fixed", False)

        class Compute:
            async def compute(self, *args):
                raise AssertionError("revoked bytes must never reach compute")

        worker = ComparisonDiffWorker(
            kernel, lambda *_: ComparisonArtifactReader(kernel.comparisons, Objects()), Compute()
        )
        assert await asyncio.wait_for(worker.process_pending(), 10) == 1
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
        )
        row = (
            await db.execute(
                text("SELECT status,body FROM comparison_diff_jobs WHERE id=:id"),
                {"id": queued["job_id"]},
            )
        ).one()
        assert tuple(row) == ("failed", None)
        assert (
            await db.scalar(
                text("SELECT count(*) FROM comparison_diff_pages WHERE job_id=:id"),
                {"id": queued["job_id"]},
            )
            == 0
        )


async def test_retained_mixed_admission_configuration_identity_survives_source_cleanup(datasets):
    service, scope, principal, *_ = datasets
    runs, configurations = (
        [await make_run(scope) for _ in range(2)],
        [str(uuid4()) for _ in range(2)],
    )
    async with execution_admin_session() as db:
        for run, config in zip(runs, configurations, strict=True):
            await db.execute(
                text(
                    'INSERT INTO execution_configurations(id,run_id,body,purpose,owner_user_id,created_by) VALUES(:id,:run,\'{"stage":"admission","private":"never-retain"}\',\'production\',:owner,:owner)'
                ),
                {"id": config, "run": run, "owner": principal.user_id},
            )
        await db.commit()
    repo, identity, revision = await capture(service, scope, principal, runs)
    before = (await repo.read(scope, principal, identity, revision)).body
    assert {member["admission_configuration_id"] for member in before["members"]} == set(
        configurations
    )
    assert "never-retain" not in str(before)
    async with execution_admin_session() as db:
        # Accounting admission rows are immutable. Only mutable projection/history
        # data is cleaned; retaining fixed facts must not depend on those generations.
        removed = await db.execute(
            text("DELETE FROM execution_view_observations WHERE run_id=ANY(:runs)"), {"runs": runs}
        )
        assert removed.rowcount > 0
        await db.execute(
            text(
                "UPDATE execution_view_runs SET projection_revision=projection_revision+100 WHERE run_id=ANY(:runs)"
            ),
            {"runs": runs},
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM execution_configurations WHERE id=ANY(:ids)"),
                {"ids": configurations},
            )
            == 2
        )
        await db.commit()
    assert (await repo.read(scope, principal, identity, revision)).body == before


async def test_selected_result_and_batch_total_parity_and_accounting_only_revocation(
    budget_binding_fixture, datasets
):
    from dataclasses import replace
    from datetime import timedelta
    from uuid import uuid4

    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.execution.test_postgres_execution_view import write
    from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

    _, scope, principal, *_ = budget_binding_fixture
    _, _, batch, candidate = await completed(budget_binding_fixture)
    await seed_model_score(
        budget_binding_fixture, candidate, request_id="comparison-accounting-model"
    )
    repo = repository(datasets[0])
    retry, other, other_result = uuid4(), uuid4(), uuid4()
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
        )
        admitted = await db.scalar(
            text("SELECT admitted_at FROM execution_view_runs WHERE run_id=:run"),
            {"run": candidate.run_id},
        )
        await db.execute(
            text(
                "INSERT INTO evaluation_batch_attempts(result_id,attempt,run_id,command_id,admission_key,owner_user_id,created_by) VALUES(:result,1,:run,:command,:key,:owner,:owner)"
            ),
            {
                "result": candidate.result_id,
                "run": retry,
                "command": uuid4(),
                "key": str(uuid4()),
                "owner": principal.user_id,
            },
        )
        await db.execute(
            text(
                "INSERT INTO evaluation_batch_results(id,batch_id,case_revision_id,config_version_id,repetition,ordinal,owner_user_id,created_by) SELECT :id,batch_id,case_revision_id,config_version_id,1,1,owner_user_id,created_by FROM evaluation_batch_results WHERE id=:result"
            ),
            {"id": other_result, "result": candidate.result_id},
        )
        await db.execute(
            text(
                "INSERT INTO evaluation_batch_attempts(result_id,attempt,run_id,command_id,admission_key,owner_user_id,created_by) VALUES(:result,0,:run,:command,:key,:owner,:owner)"
            ),
            {
                "result": other_result,
                "run": other,
                "command": uuid4(),
                "key": str(uuid4()),
                "owner": principal.user_id,
            },
        )
        await db.commit()
    for run in (retry, other):
        await write(
            run,
            scope,
            1,
            {
                "family": "agent",
                "status": "completed",
                "admitted_at": (admitted - timedelta(days=2)).isoformat(),
                "terminal_at": (admitted - timedelta(days=2) + timedelta(seconds=1)).isoformat(),
            },
        )
    async with execution_admin_session() as db:
        for run, cost in [(candidate.run_id, 1), (retry, 2), (other, 4)]:
            call, config = str(uuid4()), str(uuid4())
            values = {
                "owner": principal.user_id,
                "run": run,
                "call": call,
                "config": config,
                "activity": uuid4(),
                "event": uuid4(),
                "fact": '{"cost_usd":"' + str(cost) + '"}',
            }
            await db.execute(
                text(
                    "INSERT INTO execution_configurations(id,run_id,body,purpose,owner_user_id,created_by) VALUES(:config,:run,'{}','evaluation_subject',:owner,:owner)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO execution_model_dispatches(call_identity,run_id,activity_id,generation,claim_generation,attempt_id,logical_group,ordinal,configuration_id,request_snapshot,owner_user_id,created_by) VALUES(:call,:run,:activity,0,1,:call,'physical',1,:config,'{}',:owner,:owner)"
                ),
                values,
            )
            await db.execute(
                text(
                    "INSERT INTO execution_model_settlements(call_identity,fact,owner_user_id,created_by) VALUES(:call,CAST(:fact AS jsonb),:owner,:owner)"
                ),
                values,
            )
            for phase in ("dispatch", "settlement"):
                await db.execute(
                    text(
                        "INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,created_by) VALUES(:call,:phase,:event,1,:owner,:owner)"
                    ),
                    {**values, "phase": phase, "event": uuid4()},
                )
        await db.commit()
    session = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO sessions(id,owner_user_id) VALUES(:id,:owner)"),
            {"id": session, "owner": principal.user_id},
        )
        await db.commit()
    await write(retry, scope, 2, {"source": {"entity_type": "session", "entity_id": session}})
    filters = {
        "start": (admitted - timedelta(seconds=1)).isoformat(),
        "end": (admitted + timedelta(seconds=1)).isoformat(),
    }
    captures = {}
    for mode, expected_count, expected_cost in [
        ("selected_result", 2, "3"),
        ("batch_total", 3, "7"),
    ]:
        selected_filters = {**filters, "accounting": mode}
        if mode == "batch_total":
            selected_filters["batch_id"] = str(batch.id)
        identity, revision = await repo.materialize(
            scope,
            principal,
            comparison_request({"mode": "all_matching", "filters": selected_filters}),
        )
        body = (await repo.read(scope, principal, identity, revision)).body
        assert body["member_count"] == 1
        assert body["metrics"]["usage"]["accounting_run_count"] == expected_count
        assert (
            body["metrics"]["usage"]["purposes"]["evaluation_subject"]["cost_usd"]["value"]
            == expected_cost
        )
        assert body["metrics"]["accounting_coverage"] == "complete"
        captures[mode] = identity, revision
        from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
            analysis_repository,
        )

        a01 = await analysis_repository(datasets[0]).capture(
            scope, principal, AnalysisQuery.parse(selected_filters, "day", "UTC"), None
        )
        assert body["metrics"]["usage"] == a01.metrics["usage"]
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE sessions SET deleted_at=clock_timestamp() WHERE id=:id"), {"id": session}
        )
        await db.commit()
    for mode, expected_cost in [("selected_result", "1"), ("batch_total", "5")]:
        body = (await repo.read(scope, principal, *captures[mode])).body
        assert body["member_count"] == 1
        assert body["coverage_changed"] is True
        assert body["metrics"]["accounting_coverage"] == "partial_unavailable"
        assert body["metrics"]["coverage"] == "partial_unavailable"
        assert (
            body["metrics"]["usage"]["purposes"]["evaluation_subject"]["cost_usd"]["value"]
            == expected_cost
        )
        assert str(retry) not in str(body)


async def test_100k_cohort_targeted_artifact_check_and_large_publish_have_bounded_work(
    datasets, isolated_database
):
    repo, scope, principal, _old_id, _old_revision, selection = await fixed_artifact_comparison(
        datasets
    )
    run = selection["left"]["run_id"]
    async with execution_admin_session() as db:
        await db.execute(
            text("""INSERT INTO execution_view_runs(run_id,family,status,purpose,admitted_at,terminal_at,completeness,capabilities,projection_revision,projector_version,formal_position,progress_position,observed_order,owner_user_id,team_id,created_by)
          SELECT gen_random_uuid(),family,status,purpose,admitted_at,terminal_at,completeness,capabilities,projection_revision,projector_version,formal_position,progress_position,observed_order,owner_user_id,team_id,created_by FROM execution_view_runs CROSS JOIN generate_series(1,99999) WHERE run_id=:run"""),
            {"run": run},
        )
        await db.commit()
    identity, revision = await repo.materialize(
        scope, principal, comparison_request({"mode": "all_matching", "detail_run_ids": [run]})
    )
    assert (await repo.read(scope, principal, identity, revision, limit=1)).body[
        "member_count"
    ] == 100000
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=repo.secret,
        )
        capture_id = await db.scalar(
            text(
                "SELECT id FROM comparison_revisions WHERE comparison_id=:id AND revision=:revision"
            ),
            {"id": identity, "revision": revision},
        )
        plan = await db.scalar(
            text(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM opencitadel_comparison_current_rows(:scope,:capture,CAST(:selected AS uuid[]))"
            ),
            {"scope": "user:" + principal.user_id, "capture": capture_id, "selected": [run]},
        )
        assert plan[0]["Plan"]["Actual Rows"] == 1
        # All internal function buffer accesses are included in Function Scan totals.
        # Threshold is deliberately far below a whole 100k-row cohort scan.
        assert (
            plan[0]["Plan"].get("Shared Hit Blocks", 0)
            + plan[0]["Plan"].get("Shared Read Blocks", 0)
            < 10000
        )
        assert plan[0]["Execution Time"] < 5000
        for signature in (
            "opencitadel_comparison_resource_rows(text,uuid,uuid[])",
            "opencitadel_comparison_current_rows(text,uuid,uuid[])",
            "opencitadel_comparison_diff_authorized(text,uuid,jsonb)",
        ):
            definition = await db.scalar(
                text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
                {"signature": signature},
            )
            assert "opencitadel_analysis_authority" not in definition
            assert "opencitadel_analysis_resources_available" not in definition
    jobs = DBComparisonDiffJobs(repo)
    queued = await jobs.enqueue(
        scope, principal, identity, revision, selection, request_id="large-publish"
    )
    async with kernel_jobs(repo, isolated_database) as (kernel, _):
        job = await kernel.claim()
        assert job["id"] == queued["job_id"]
        await asyncio.wait_for(
            kernel.publish(
                job,
                scope,
                principal,
                {
                    "left": {},
                    "right": {},
                    "format": "text",
                    "diff": {
                        "complete": True,
                        "content_changed": True,
                        "reason": None,
                        "content": "x" * 900000,
                        "operations": [],
                    },
                },
            ),
            10,
        )
    page = await jobs.page(scope, principal, queued["job_id"])
    assert page["status"] == "complete"
    assert page["result"]["page_count"] <= 16


async def test_authorized_team_member_cannot_read_other_callers_job_or_job_cursor(
    datasets, isolated_database
):
    service, scope, first, other_scope, second = await team_callers(datasets)
    repo, _, _, identity, revision, selection = await fixed_artifact_comparison(
        (service, scope, first)
    )
    assert (await repo.read(other_scope, second, identity, revision)).body["member_count"] == 1
    jobs = DBComparisonDiffJobs(repo)
    queued = await jobs.enqueue(
        scope, first, identity, revision, selection, request_id="private-job"
    )
    with pytest.raises(ValueError, match="artifact_unavailable"):
        await jobs.page(other_scope, second, queued["job_id"])
    with pytest.raises(ValueError, match="cursor"):
        await jobs.page(
            other_scope,
            second,
            queued["job_id"],
            cursor=repo._cursor(scope, first, queued["job_id"], 0, 1),
        )
    # A second authorized caller can enqueue their own job, but cannot replay
    # the first caller's receipt even for identical team-visible artifacts.
    own = await jobs.enqueue(
        other_scope, second, identity, revision, selection, request_id="private-job"
    )
    assert own != queued
