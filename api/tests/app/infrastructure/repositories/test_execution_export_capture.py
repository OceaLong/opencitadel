"""Real PostgreSQL boundaries; local authorization permits collection only."""

# ruff: noqa: F401,F811
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_execution_export_repository import (
    DBExecutionExportRepository,
)
from app.infrastructure.security.db_authorization import (
    configure_sync_authorization,
    configure_sync_system_authorization,
)
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
    capture,
    make_run,
)

pytestmark = pytest.mark.asyncio


@contextmanager
def authorized_read(engine, scope, principal):
    """Read private tables through the migration owner under signed scope RLS."""
    with engine.connect() as db:
        configure_sync_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        yield db


@contextmanager
def authorized_write(engine, scope, principal):
    """Mutate fixture rows with the same signed scope as the repository."""
    with engine.begin() as db:
        configure_sync_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        yield db


@contextmanager
def authorized_system_write(engine):
    """Seed identity rows through the signed system context they require."""
    with engine.begin() as db:
        configure_sync_system_authorization(
            db,
            actor="execution-export-test",
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        yield db


def repository(service):
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        analysis_repository,
    )

    analysis = analysis_repository(service)
    return DBExecutionExportRepository(analysis.session_factory, signing_secret=analysis.secret)


def request(run_id, key=None):
    return {
        "source_kind": "filter",
        "format": "json",
        "request_id": key or str(uuid4()),
        "selection": {"mode": "explicit", "run_ids": [run_id]},
    }


async def test_capture_replay_retains_identical_job_and_rejects_changed_intent(datasets):
    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    repo = repository(service)
    payload = request(run_id)
    first = await repo.accept(scope, principal, payload)
    second = await repo.accept(scope, principal, payload)
    assert first["id"] == second["id"]
    with pytest.raises(ValueError, match="export_request_conflict"):
        await repo.accept(scope, principal, {**payload, "format": "csv"})
    status = await repo.get(scope, principal, first["id"])
    assert status["status"] == "queued"
    assert status["row_count"] == 1


async def test_seal_failure_rolls_back_receipt_and_fixed_capture(
    datasets, monkeypatch, isolated_database
):
    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    repo = repository(service)
    payload = request(run_id)
    engine, _ = isolated_database
    tables = (
        "execution_exports",
        "export_receipts",
        "export_staging",
        "export_rows",
        "export_source_facts",
        "export_resources",
        "comparison_sets",
        "comparison_revisions",
        "comparison_members",
        "comparison_usage",
        "comparison_scores",
        "comparison_allocations",
        "comparison_accounting",
        "comparison_accounting_links",
        "comparison_tool_facts",
        "comparison_tool_captures",
        "comparison_receipts",
        "resource_pins",
    )
    with authorized_read(engine, scope, principal) as db:
        before = {name: db.scalar(text(f"SELECT count(*) FROM {name}")) for name in tables}
    seal = repo._seal

    async def fail_after_copy(*args):
        await seal(*args)
        raise ValueError("injected_after_seal")

    monkeypatch.setattr(repo, "_seal", fail_after_copy)
    with pytest.raises(ValueError, match="injected_after_seal"):
        await repo.accept(scope, principal, payload)
    with authorized_read(engine, scope, principal) as db:
        assert {name: db.scalar(text(f"SELECT count(*) FROM {name}")) for name in tables} == before
    monkeypatch.setattr(repo, "_seal", seal)
    created = await repo.accept(scope, principal, payload)
    assert not created.get("replayed")


async def test_export_private_tables_and_staging_helpers_have_no_runtime_grants(datasets):
    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        for table in (
            "execution_exports",
            "export_staging",
            "export_quota_serializers",
            "export_receipts",
            "export_rows",
            "export_source_facts",
            "export_resources",
            "export_object_intents",
            "export_download_uses",
        ):
            assert not await work.db_session.scalar(
                text(
                    "SELECT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                ),
                {"table": table},
            )
        for helper in (
            "opencitadel_export_retire(uuid)",
            "opencitadel_export_source_facts(text,text)",
            "opencitadel_export_current_rows(text,uuid,uuid[])",
        ):
            assert not await work.db_session.scalar(
                text("SELECT has_function_privilege(current_user,:helper,'EXECUTE')"),
                {"helper": helper},
            )


async def test_export_acceptance_does_not_change_published_comparison(datasets):
    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    comparisons, identity, revision = await capture(service, scope, principal, [run_id])
    before = (await comparisons.read(scope, principal, identity, revision)).body
    repo = repository(service)
    await repo.accept(
        scope,
        principal,
        {
            "source_kind": "comparison",
            "format": "csv",
            "comparison_id": identity,
            "revision": revision,
            "request_id": str(uuid4()),
        },
    )
    after = (await comparisons.read(scope, principal, identity, revision)).body
    assert before == after


async def test_success_leaves_no_staging_or_hidden_comparison_and_parity(
    datasets, isolated_database
):
    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    comparisons, identity, revision = await capture(service, scope, principal, [run_id])
    expected = (await comparisons.read(scope, principal, identity, revision)).body["metrics"]
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        before = db.scalar(text("SELECT count(*) FROM comparison_sets"))
    repo = repository(service)
    accepted = await repo.accept(scope, principal, request(run_id))
    with authorized_read(engine, scope, principal) as db:
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0
        assert db.scalar(text("SELECT count(*) FROM comparison_sets")) == before
        assert db.scalar(text("SELECT count(*) FROM comparison_revisions WHERE NOT published")) == 0
        header = db.scalar(
            text("SELECT header FROM execution_exports WHERE id=CAST(:id AS uuid)"),
            {"id": accepted["id"]},
        )
    for name in ("series", "usage", "intervals", "approvals", "scores", "charts"):
        assert header["metrics"][name] == expected[name]


async def test_concurrent_receipt_replay_returns_one_job(datasets):
    import asyncio

    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    repo = repository(service)
    payload = request(run_id)
    first, second = await asyncio.gather(
        repo.accept(scope, principal, payload), repo.accept(scope, principal, payload)
    )
    assert first["id"] == second["id"]


async def test_expired_receipt_never_creates_new_capture(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    repo = repository(service)
    payload = request(run_id)
    accepted = await repo.accept(scope, principal, payload)
    engine, _ = isolated_database
    with authorized_write(engine, scope, principal) as db:
        changed = db.execute(
            text(
                "UPDATE execution_exports SET expires_at=clock_timestamp()-interval '1 second' WHERE id=CAST(:id AS uuid)"
            ),
            {"id": accepted["id"]},
        )
        assert changed.rowcount == 1
    replay = await repo.accept(scope, principal, payload)
    assert replay["id"] == accepted["id"]
    assert replay["replayed"]
    assert await repo.get(scope, principal, accepted["id"]) == {
        "id": accepted["id"],
        "status": "expired",
    }


async def test_raw_sql_authority_revocation_blocks_existing_export(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    repo = repository(service)
    accepted = await repo.accept(scope, principal, request(await make_run(scope)))
    engine, _ = isolated_database
    with authorized_system_write(engine) as db:
        changed = db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        assert changed.rowcount == 1
    with pytest.raises(PermissionError):
        await repo.get(scope, principal, accepted["id"])


async def test_atomic_active_quota_rolls_back_sixth_capture(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    repo = repository(service)
    run_id = await make_run(scope)
    for _ in range(5):
        await repo.accept(scope, principal, request(run_id))
    with pytest.raises(ValueError, match="export_quota_exceeded"):
        await repo.accept(scope, principal, request(run_id))
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        assert db.scalar(text("SELECT count(*) FROM execution_exports")) == 5
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0


async def test_full_100000_run_capture_and_100001_exclusion_boundary(datasets, isolated_database):
    """Full declared primary cardinality. No throughput claim until this actually runs."""
    from app.infrastructure.models.execution_view import execution_view_runs

    service, scope, principal, *_ = datasets
    original = await make_run(scope)
    engine, _ = isolated_database
    assert engine.url.database.startswith("test_execution_view_")
    columns = [c.name for c in execution_view_runs.columns if c.name != "scope_key"]
    # Trusted model columns only; reproduce the real persisted reader row shape.
    selected = ["gen_random_uuid()" if c == "run_id" else "template." + c for c in columns]
    with authorized_write(engine, scope, principal) as db:
        db.execute(
            text(
                "INSERT INTO execution_view_runs ("
                + ",".join(columns)
                + ") SELECT "
                + ",".join(selected)
                + " FROM execution_view_runs template CROSS JOIN generate_series(1,100000) WHERE template.run_id=CAST(:id AS uuid)"
            ),
            {"id": original},
        )
    repo = repository(service)
    payload = {
        "source_kind": "filter",
        "format": "json",
        "request_id": str(uuid4()),
        "selection": {"mode": "all_matching"},
    }
    with pytest.raises(ValueError, match="comparison_capacity_exceeded"):
        await repo.accept(scope, principal, payload)
    accepted = await repo.accept(
        scope,
        principal,
        {
            **payload,
            "request_id": str(uuid4()),
            "selection": {"mode": "all_matching", "excluded_run_ids": [original]},
        },
    )
    assert (await repo.get(scope, principal, accepted["id"]))["row_count"] == 100000
    with authorized_read(engine, scope, principal) as db:
        assert (
            db.scalar(
                text("SELECT count(*) FROM export_rows WHERE capture_id=CAST(:id AS uuid)"),
                {"id": accepted["id"]},
            )
            == 100000
        )
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0

    import json

    from app.application.services.execution_export_download import ExportDownloader
    from app.application.services.execution_export_worker import ExecutionExportWorker
    from tests.app.infrastructure.repositories.test_execution_export_lifecycle import (
        PrivateObjects,
        kernel_repository,
    )

    objects = PrivateObjects()
    async with kernel_repository(repo) as kernel:
        await ExecutionExportWorker(kernel, objects).process_pending()
    assert (await repo.get(scope, principal, accepted["id"]))["status"] == "ready"
    spool, _, _ = await ExportDownloader(repo, objects).prepare(scope, principal, accepted["id"])
    try:
        assert len(json.load(spool)["rows"]) == 100000
    finally:
        spool.close()


async def test_successful_sql_seal_commits_job_and_receipt(datasets, isolated_database):
    service, scope, principal, *_ = datasets
    payload = request(await make_run(scope))
    result = await repository(service).accept(scope, principal, payload)
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        assert (
            db.scalar(
                text("SELECT status FROM execution_exports WHERE id=CAST(:id AS uuid)"),
                {"id": result["id"]},
            )
            == "queued"
        )
        assert (
            str(
                db.scalar(
                    text("SELECT export_id FROM export_receipts WHERE request_id=:request"),
                    {"request": payload["request_id"]},
                )
            )
            == result["id"]
        )


@pytest.mark.parametrize("same_request", [False, True])
async def test_overlapping_rr_snapshots_retry_quota_and_receipt(
    datasets, isolated_database, monkeypatch, same_request
):
    import asyncio

    service, scope, principal, *_ = datasets
    repo = repository(service)
    run = await make_run(scope)
    for _ in range(4):
        await repo.accept(scope, principal, request(run))
    signed, barrier, entered = repo._signed, asyncio.Event(), 0

    async def synchronized_sign(*args, **kwargs):
        nonlocal entered
        result = await signed(*args, **kwargs)
        if args[3] == "accept" and entered < 2:
            entered += 1
            if entered == 2:
                barrier.set()
            await barrier.wait()
        return result

    monkeypatch.setattr(repo, "_signed", synchronized_sign)
    first = request(run)
    second = first if same_request else request(run)
    results = await asyncio.gather(
        repo.accept(scope, principal, first),
        repo.accept(scope, principal, second),
        return_exceptions=True,
    )
    if same_request:
        assert all(isinstance(item, dict) for item in results)
        assert results[0]["id"] == results[1]["id"]
        assert any(item.get("replayed") for item in results)
    else:
        assert sum(isinstance(item, dict) for item in results) == 1
        assert any(
            isinstance(item, ValueError) and str(item) == "export_quota_exceeded"
            for item in results
        )
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        assert (
            db.scalar(
                text("SELECT count(*) FROM execution_exports WHERE caller_id=:caller"),
                {"caller": principal.user_id},
            )
            == 5
        )
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE comparison_revisions SET published=false WHERE id=:id",
        "DELETE FROM comparison_revisions WHERE id=:id",
        "DELETE FROM comparison_members WHERE capture_id=:id",
    ],
)
async def test_published_capture_mutation_is_forbidden(datasets, isolated_database, mutation):
    from sqlalchemy.exc import DBAPIError

    service, scope, principal, *_ = datasets
    _, identity, revision = await capture(service, scope, principal, [await make_run(scope)])
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        capture_id = db.scalar(
            text(
                "SELECT id FROM comparison_revisions WHERE comparison_id=CAST(:id AS uuid) AND revision=:revision"
            ),
            {"id": identity, "revision": revision},
        )
    with (
        pytest.raises(DBAPIError, match="comparison_immutable"),
        authorized_write(engine, scope, principal) as db,
    ):
        db.execute(text(mutation), {"id": capture_id})


async def test_runtime_cannot_forge_private_staging_marker(datasets):
    from sqlalchemy.exc import DBAPIError

    service, scope, principal, *_ = datasets
    repo = repository(service)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with repo.transactions.transaction(scope, principal) as db:
            await db.execute(
                text(
                    "INSERT INTO export_staging(capture_id,comparison_id,scope_key,caller_id,request_id) VALUES(:id,:comparison,:scope,:caller,'forged')"
                ),
                {
                    "id": uuid4(),
                    "comparison": uuid4(),
                    "scope": "user:" + principal.user_id,
                    "caller": principal.user_id,
                },
            )


@pytest.mark.parametrize("attack", ["chosen_target", "cross_scope", "internal_receipt"])
async def test_signed_staging_rejects_target_scope_and_internal_receipt_collision(
    datasets, isolated_database, attack
):
    import json
    from dataclasses import asdict

    from sqlalchemy.exc import DBAPIError

    from app.application.ports.execution_comparison import ComparisonRequest

    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    _, identity, _ = await capture(service, scope, principal, [run])
    engine, _ = isolated_database
    with authorized_read(engine, scope, principal) as db:
        collision = db.scalar(
            text("SELECT request_id FROM comparison_receipts WHERE caller_id=:caller LIMIT 1"),
            {"caller": principal.user_id},
        )
        before = db.scalar(text("SELECT count(*) FROM comparison_sets"))
    repo = repository(service)
    with pytest.raises(DBAPIError, match=r"export_staging_invalid|analysis_authorization"):  # noqa: PT012 - entire transaction must roll back
        async with repo.transactions.transaction(scope, principal) as db:
            body = asdict(
                ComparisonRequest.parse(
                    {
                        "mode": "explicit",
                        "run_ids": [run],
                        "request_id": collision if attack == "internal_receipt" else str(uuid4()),
                        "detail_run_ids": [],
                    }
                )
            )
            body = json.loads(json.dumps(body, default=str))
            if attack == "chosen_target":
                body["comparison_id"] = identity
            source = await repo._signed(db, scope, principal, "materialize", **body)
            if attack == "cross_scope":
                import hashlib
                import hmac

                forged = json.loads(source["body"])
                forged["scope"] = "user:other"
                source["body"] = json.dumps(forged, separators=(",", ":"), sort_keys=True)
                source["signature"] = hmac.new(
                    repo.secret.encode(),
                    ("opencitadel:analysis:authority:v1:" + source["body"]).encode(),
                    hashlib.sha256,
                ).hexdigest()
            accepted = await repo._signed(
                db,
                scope,
                principal,
                "accept",
                request=request(run),
                owner_scope=scope.model_dump(mode="json"),
            )
            await db.scalar(
                text(
                    "SELECT opencitadel_export_accept(:body,:signature,:source_encoded,:source_signature)"
                ),
                {
                    **accepted,
                    "source_encoded": source["body"],
                    "source_signature": source["signature"],
                },
            )
    with authorized_read(engine, scope, principal) as db:
        assert db.scalar(text("SELECT count(*) FROM export_staging")) == 0
        assert db.scalar(text("SELECT count(*) FROM execution_exports")) == 0
        assert db.scalar(text("SELECT count(*) FROM comparison_sets")) == before


async def test_same_team_other_caller_cannot_read_status_or_content(datasets, isolated_database):
    from app.application.services.execution_export_download import ExportDownloader
    from app.application.services.execution_export_worker import ExecutionExportWorker
    from app.domain.models.scope import OwnerScope, Principal
    from app.domain.models.team import TeamRole
    from tests.app.infrastructure.repositories.test_execution_export_lifecycle import (
        PrivateObjects,
        kernel_repository,
    )

    service, _, principal, *_ = datasets
    team, other = str(uuid4()), "export-other-" + uuid4().hex
    engine, _ = isolated_database
    with authorized_system_write(engine) as db:
        db.execute(
            text("INSERT INTO users(id,email,username) VALUES(:id,:email,:id)"),
            {"id": other, "email": other + "@test.invalid"},
        )
        db.execute(text("INSERT INTO teams(id,name) VALUES(:id,'export-private')"), {"id": team})
        for user in (principal.user_id, other):
            db.execute(
                text("INSERT INTO team_members(team_id,user_id) VALUES(:team,:user)"),
                {"team": team, "user": user},
            )
    principal = principal.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
    scope = OwnerScope.team(principal.user_id, team)
    repo = repository(service)
    accepted = await repo.accept(scope, principal, request(await make_run(scope)))
    objects = PrivateObjects()
    async with kernel_repository(repo) as kernel:
        await ExecutionExportWorker(kernel, objects).process_pending()
    assert (await repo.get(scope, principal, accepted["id"]))["status"] == "ready"
    outsider = Principal(user_id=other, team_roles={team: TeamRole.MEMBER})
    outsider_scope = OwnerScope.team(other, team)
    with pytest.raises(ValueError, match="export_not_found"):
        await repo.get(outsider_scope, outsider, accepted["id"])
    with pytest.raises(ValueError, match="export_not_found"):
        await ExportDownloader(repo, objects).prepare(outsider_scope, outsider, accepted["id"])


async def test_serialization_retry_recaptures_from_fresh_snapshot(
    datasets, isolated_database, monkeypatch
):
    import asyncio

    service, scope, principal, *_ = datasets
    repo, run = repository(service), await make_run(scope)
    signed, both, release = repo._signed, asyncio.Event(), asyncio.Event()
    entered = 0

    async def coordinated(*args, **kwargs):
        nonlocal entered
        result = await signed(*args, **kwargs)
        if args[3] == "accept" and entered < 2:
            entered += 1
            order = entered
            if order == 2:
                both.set()
                await release.wait()
            else:
                await both.wait()
        return result

    monkeypatch.setattr(repo, "_signed", coordinated)
    first = asyncio.create_task(repo.accept(scope, principal, request(run)))
    second = asyncio.create_task(repo.accept(scope, principal, request(run)))
    old = await first
    engine, _ = isolated_database
    with authorized_write(engine, scope, principal) as db:
        changed = db.execute(
            text(
                "UPDATE execution_view_runs SET status='failed',projection_revision=projection_revision+1 WHERE run_id=CAST(:id AS uuid)"
            ),
            {"id": run},
        )
        assert changed.rowcount == 1
    release.set()
    new = await second
    with authorized_read(engine, scope, principal) as db:
        statuses = [
            db.scalar(
                text(
                    "SELECT body->'run_fact'->>'status' FROM export_rows WHERE capture_id=CAST(:id AS uuid) AND ordinal=0"
                ),
                {"id": item["id"]},
            )
            for item in (old, new)
        ]
    assert statuses == ["completed", "failed"]
