"""Actual owned PostgreSQL acceptance gates; collect only while infrastructure is deferred."""

# ruff: noqa: F401,F811
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.execution.test_postgres_execution_view import write
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
    analysis_repository,
)

pytestmark = pytest.mark.asyncio


def repository(service):
    from app.infrastructure.repositories.db_execution_comparison_repository import (
        DBExecutionComparisonRepository,
    )

    original = analysis_repository(service)
    return DBExecutionComparisonRepository(original.session_factory, signing_secret=original.secret)


def comparison_request(payload):
    from app.application.ports.execution_comparison import ComparisonRequest

    return ComparisonRequest.parse({"request_id": str(uuid4()), **payload})


async def make_run(scope, *, status="completed"):
    identity = uuid4()
    await write(
        identity,
        scope,
        1,
        {
            "family": "agent",
            "status": status,
            "admitted_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "terminal_at": datetime.now(UTC).isoformat(),
        },
    )
    return str(identity)


async def capture(service, scope, principal, run_ids=None):
    from app.application.ports.execution_comparison import ComparisonRequest

    request = comparison_request(
        {"mode": "explicit" if run_ids else "all_matching", "run_ids": run_ids or []}
    )
    repo = repository(service)
    identity, revision = await repo.materialize(scope, principal, request)
    return repo, identity, revision


@pytest.mark.parametrize("team_workspace", [False, True])
async def test_session_creator_metadata_preserves_scoped_comparison_authority(
    datasets, team_workspace
):
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from app.domain.models.scope import OwnerScope
    from app.domain.models.team import TeamRole
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    session_id, creator, team = str(uuid4()), principal.user_id, str(uuid4())
    if team_workspace:
        creator = str(uuid4())
        scope = OwnerScope.team(principal.user_id, team)
        principal = principal.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
    async with execution_admin_session() as db:
        if team_workspace:
            await db.execute(
                text("INSERT INTO users(id,email,username) VALUES(:id,:email,:id)"),
                {"id": creator, "email": creator + "@test.invalid"},
            )
            await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,'session')"), {"id": team})
            await db.execute(
                text("INSERT INTO team_members(team_id,user_id) VALUES(:team,:user)"),
                {"team": team, "user": principal.user_id},
            )
        await db.execute(
            text("INSERT INTO sessions(id,owner_user_id,team_id) VALUES(:id,:creator,:team)"),
            {"id": session_id, "creator": creator, "team": scope.team_id},
        )
        await db.execute(
            text("""INSERT INTO artifacts(id,session_id,kind,title,version_refs)
            VALUES(:id,:session,'doc','retained creator','["fixed"]')"""),
            {"id": "artifact-" + session_id, "session": session_id},
        )
        await db.commit()
    resource = ResourceIdentity(
        resource_kind="artifact", resource_id="artifact-" + session_id, resource_version="1"
    )
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        await DBResourcePinRepository(work.db_session).acquire(
            scope, "session", session_id, [resource]
        )
        await work.commit()
    run_id = await make_run(scope)
    await write(
        UUID(run_id), scope, 2, {"source": {"entity_type": "session", "entity_id": session_id}}
    )
    repo, comparison_id, revision = await capture(service, scope, principal, [run_id])
    assert (await repo.read(scope, principal, comparison_id, revision)).body["member_count"] == 1
    # A distinct personal owner still cannot read this artifact, including when
    # that owner is a legitimate member of its team workspace.
    foreign_scope = OwnerScope.personal(creator if team_workspace else str(uuid4()))
    async with execution_admin_session() as db:
        with pytest.raises(ResourceUnavailable):
            await DBResourcePinRepository(db).resolve(foreign_scope, resource)
    if team_workspace:
        async with execution_admin_session() as db:
            with pytest.raises(ResourceUnavailable):
                await DBResourcePinRepository(db).resolve(
                    OwnerScope.team(principal.user_id, str(uuid4())), resource
                )
            await db.execute(
                text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
                {"team": team, "user": principal.user_id},
            )
            await db.commit()
        with pytest.raises(PermissionError):
            await repo.read(scope, principal, comparison_id, revision)


@pytest.mark.parametrize(("phase", "step_kind"), [("input", "model"), ("output", "tool")])
async def test_redacted_model_input_is_not_a_required_comparison_resource(
    datasets, phase, step_kind
):
    """PostgreSQL keeps the redacted fact boundary without making normal runs unavailable."""
    import hashlib

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    run_id = await make_run(scope)
    await write(
        UUID(run_id),
        scope,
        2,
        {"kind": step_kind, "status": "completed", "activity_id": str(uuid4())},
        kind="step",
        identity="model-step",
    )
    content_id, event_id, command_id = uuid4(), uuid4(), uuid4()
    body = '{"message":"publicly redacted"}'
    digest = hashlib.sha256(body.encode()).hexdigest()
    async with execution_admin_session() as db:
        await db.execute(
            text("""INSERT INTO execution_public_content
              (content_id,command_id,run_id,activity_id,generation,claim_generation,
               phase,body,content_digest,byte_length,redacted,citation_refs,owner_user_id,created_by)
              VALUES (:content,:command,:run,:activity,0,1,:phase,:body,:digest,:length,true,'[]',:owner,'analysis-test')"""),
            {
                "content": content_id,
                "command": command_id,
                "run": run_id,
                "activity": uuid4(),
                "body": body,
                "digest": digest,
                "length": len(body),
                "owner": scope.user_id,
                "phase": phase,
            },
        )
        await db.execute(
            text("""INSERT INTO execution_content_bindings
              (event_id,phase,content_id,run_id,step_id,formal_position,owner_user_id,created_by)
              VALUES (:event,:phase,:content,:run,'model-step',2,:owner,'analysis-test')"""),
            {
                "event": event_id,
                "content": content_id,
                "run": run_id,
                "owner": scope.user_id,
                "phase": phase,
            },
        )
        await db.commit()

    repo, comparison_id, revision = await capture(service, scope, principal, [run_id])
    result = await repo.read(scope, principal, comparison_id, revision)
    assert result.body["member_count"] == 1
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.for_principal(principal, scope=scope)
        )
        resources = await db.scalar(
            text("""SELECT resources FROM comparison_resources r
              JOIN comparison_revisions v ON v.id=r.capture_id
              WHERE v.comparison_id=:comparison AND v.revision=:revision"""),
            {"comparison": comparison_id, "revision": revision},
        )
    assert all(item["resource_id"] != str(content_id) for item in resources)


@pytest.mark.parametrize("phase", ["input", "output"])
async def test_recording_source_redacted_pin_is_not_a_readable_comparison_resource(
    budget_binding_fixture, datasets, phase
):
    """Redacted source pins stay in replay authority but outside readable refs."""
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_e07_reader_integration import (
        with_write_recording,
    )
    from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

    fixture, manifest, _ = await with_write_recording(
        budget_binding_fixture, redacted_pin_phase=phase
    )
    _, _, _, candidate = await completed(fixture)
    await seed_model_score(fixture, candidate, request_id=f"recording-{phase}-comparison-model")
    pin = manifest.pins[0]

    if phase == "output":
        # Reproduce the source-run tool output shape from a full recording.
        await write(
            manifest.source_run_id,
            fixture[1],
            1,
            {"family": "agent", "status": "completed"},
        )
        await write(
            manifest.source_run_id,
            fixture[1],
            2,
            {"kind": "tool", "status": "completed", "activity_id": str(uuid4())},
            kind="step",
            identity="source/model-content",
        )

    async with execution_admin_session() as db:
        stored = await db.scalar(
            text("SELECT body->'pins' FROM evaluation_recording_versions WHERE id=:id"),
            {"id": manifest.id},
        )
        available = await db.scalar(
            text("""SELECT available FROM resource_pins
              WHERE owner_kind='recording_version' AND owner_id=:id
               AND resource_kind=:kind AND resource_id=:resource AND resource_version=:version"""),
            {
                "id": str(manifest.id),
                "kind": pin.resource_kind,
                "resource": pin.resource_id,
                "version": pin.resource_version,
            },
        )
    assert stored == [pin.model_dump(mode="json")]
    assert available is True

    service, scope, principal, *_ = datasets
    repo, comparison_id, revision = await capture(
        service, scope, principal, [str(candidate.run_id)]
    )
    result = await repo.read(scope, principal, comparison_id, revision)
    assert result.body["member_count"] == 1
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.for_principal(principal, scope=scope)
        )
        snapshot = (
            await db.execute(
                text("""SELECT resources,pins,owners FROM comparison_resources r
                  JOIN comparison_revisions v ON v.id=r.capture_id
                  WHERE v.comparison_id=:comparison AND v.revision=:revision"""),
                {"comparison": comparison_id, "revision": revision},
            )
        ).one()
    assert {"kind": "recording_version", "id": str(manifest.id)} in snapshot.owners
    assert all(item["resource_id"] != pin.resource_id for item in snapshot.resources)
    assert any(item["resource_id"] == pin.resource_id for item in snapshot.pins)
    before = await repo.current(scope, principal, comparison_id, revision)
    async with execution_admin_session() as db:
        await db.execute(
            text("""UPDATE resource_pins SET available=false,
              unavailable_reason='force_deleted',unavailable_at=clock_timestamp()
              WHERE owner_kind='recording_version' AND owner_id=:owner
               AND resource_id=:resource"""),
            {"owner": str(manifest.id), "resource": pin.resource_id},
        )
        await db.commit()
    assert (await repo.current(scope, principal, comparison_id, revision)) != before
    assert (await repo.read(scope, principal, comparison_id, revision)).body["member_count"] == 0


async def test_linked_judge_point_excludes_redacted_content_and_retains_readable_pin(
    budget_binding_fixture,
):
    """A nonmember judge run enters comparison resources through linked point capture."""
    import hashlib

    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, _, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.create(
            scope,
            principal,
            candidate,
            rubric_id=rubric.id,
            config_id=rubric.judge_config_version,
            request_id="linked-point-redaction",
            materials={"rubric": [], "evidence": {}, "unavailable": {}, "resources": []},
            namespace_id=batch.id,
        )
        await work.commit()
    await seed_model_score(budget_binding_fixture, candidate, request_id="linked-point-model")
    judge_run = intent["run_id"]
    await write(judge_run, scope, 1, {"family": "ask", "status": "completed"})
    await write(
        judge_run,
        scope,
        2,
        {"kind": "model", "status": "completed", "activity_id": str(uuid4())},
        kind="step",
        identity="judge-step",
    )
    identities = {}
    async with execution_admin_session() as db:
        for redacted in (True, False):
            content_id, body = uuid4(), '{"message":"judge point metadata"}'
            digest = hashlib.sha256(body.encode()).hexdigest()
            await db.execute(
                text("""INSERT INTO execution_public_content
                  (content_id,command_id,run_id,activity_id,generation,claim_generation,
                   phase,body,content_digest,byte_length,redacted,citation_refs,owner_user_id,created_by)
                  VALUES (:content,:command,:run,:activity,0,1,'output',:body,:digest,:length,
                   :redacted,'[]',:owner,'analysis-test')"""),
                {
                    "content": content_id,
                    "command": uuid4(),
                    "run": judge_run,
                    "activity": uuid4(),
                    "body": body,
                    "digest": digest,
                    "length": len(body),
                    "redacted": redacted,
                    "owner": scope.user_id,
                },
            )
            await db.execute(
                text("""INSERT INTO execution_content_bindings
                  (event_id,phase,content_id,run_id,step_id,formal_position,owner_user_id,created_by)
                  VALUES (:event,'output',:content,:run,'judge-step',2,:owner,'analysis-test')"""),
                {"event": uuid4(), "content": content_id, "run": judge_run, "owner": scope.user_id},
            )
            identities[redacted] = content_id
        await db.commit()

    repo = repository(suites)
    comparison_id, revision = await repo.materialize(
        scope,
        principal,
        comparison_request(
            {
                "mode": "explicit",
                "run_ids": [str(candidate.run_id)],
                "filters": {"accounting": "selected_result"},
            }
        ),
    )
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.for_principal(principal, scope=scope)
        )
        row = (
            await db.execute(
                text("""SELECT r.resources,r.capture_id FROM comparison_resources r
                  JOIN comparison_revisions v ON v.id=r.capture_id
                  WHERE v.comparison_id=:comparison AND v.revision=:revision AND r.run_id=:run"""),
                {"comparison": comparison_id, "revision": revision, "run": judge_run},
            )
        ).one_or_none()
        if row is None:
            diagnostic = (
                await db.execute(
                    text("""SELECT d.run_id,d.captured_available,
                      EXISTS(SELECT 1 FROM comparison_resources r WHERE r.capture_id=d.capture_id
                        AND r.run_id=d.run_id) AS resource_row
                      FROM analysis_point_dependencies d JOIN comparison_revisions v
                        ON v.id=d.capture_id WHERE v.comparison_id=:comparison"""),
                    {"comparison": comparison_id},
                )
            ).all()
            counts = (
                await db.execute(
                    text("""SELECT
                      (SELECT count(*) FROM comparison_members m WHERE m.capture_id=v.id),
                      (SELECT count(*) FROM comparison_scores s WHERE s.capture_id=v.id),
                      (SELECT count(*) FROM evaluation_batch_attempts a WHERE a.run_id=:subject)
                      FROM comparison_revisions v WHERE v.comparison_id=:comparison"""),
                    {"comparison": comparison_id, "subject": candidate.run_id},
                )
            ).all()
            pytest.fail(f"linked judge resource row missing: {diagnostic}; counts={counts}")
        assert await db.scalar(
            text("""SELECT EXISTS(SELECT 1 FROM analysis_point_dependencies
              WHERE capture_id=:capture AND run_id=:run AND captured_available)"""),
            {"capture": row.capture_id, "run": judge_run},
        )
    resources = {item["resource_id"] for item in row.resources}
    assert str(identities[True]) not in resources
    assert str(identities[False]) in resources
    before = await repo.current(scope, principal, comparison_id, revision)
    async with execution_admin_session() as db:
        await db.execute(
            text("""UPDATE resource_pins SET available=false,
              unavailable_reason='force_deleted',unavailable_at=clock_timestamp()
              WHERE owner_kind='comparison_revision' AND owner_id=:capture
               AND resource_id=:content"""),
            {"capture": str(row.capture_id), "content": str(identities[False])},
        )
        await db.commit()
    assert (await repo.current(scope, principal, comparison_id, revision)) != before


async def test_fixed_members_new_run_and_terminal_changes_do_not_rewrite_old_revision(datasets):
    service, scope, principal, *_ = datasets
    first = await make_run(scope)
    repo, identity, revision = await capture(service, scope, principal, [first])
    await make_run(scope, status="failed")
    before = await repo.read(scope, principal, identity, revision)
    assert before.body["member_count"] == 1
    assert before.body["members"][0]["run_id"] == first
    after = await repo.read(scope, principal, identity, revision)
    assert after.body == before.body


async def test_refresh_is_cas_and_preserves_previous_revision(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest

    service, scope, principal, *_ = datasets
    first = await make_run(scope)
    repo, identity, revision = await capture(service, scope, principal, [first])
    second = await make_run(scope)
    query = comparison_request({"mode": "explicit", "run_ids": [first, second]})
    _, newer = await repo.materialize(
        scope, principal, query, comparison_id=identity, expected_revision=revision
    )
    assert newer == revision + 1
    assert (await repo.read(scope, principal, identity, revision)).body["member_count"] == 1
    assert (await repo.read(scope, principal, identity, newer)).body["member_count"] == 2
    from dataclasses import replace

    with pytest.raises(ValueError, match="comparison_conflict"):
        await repo.materialize(
            scope,
            principal,
            replace(query, request_id=str(uuid4())),
            comparison_id=identity,
            expected_revision=revision,
        )


async def test_all_matching_exclusions_are_materialized_not_reexecuted(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest

    service, scope, principal, *_ = datasets
    first, second = await make_run(scope), await make_run(scope)
    repo = repository(service)
    query = comparison_request({"mode": "all_matching", "excluded_run_ids": [second]})
    identity, revision = await repo.materialize(scope, principal, query)
    await make_run(scope)
    result = await repo.read(scope, principal, identity, revision)
    assert [row["run_id"] for row in result.body["members"]] == [first]


async def test_api_has_no_raw_comparison_retained_fact_grants(datasets):
    from app.domain.models.authorization import AuthorizationContext

    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        for table in ("comparison_members", "comparison_revisions", "comparison_resources"):
            assert await work.db_session.scalar(
                text(
                    "SELECT NOT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                ),
                {"table": table},
            )


async def test_comparison_cursor_cannot_cross_fixed_revision(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest

    service, scope, principal, *_ = datasets
    runs = [await make_run(scope) for _ in range(2)]
    repo, identity, revision = await capture(service, scope, principal, runs)
    first = await repo.read(scope, principal, identity, revision, limit=1)
    assert first.body["next_cursor"]
    _, newer = await repo.materialize(
        scope,
        principal,
        comparison_request({"mode": "explicit", "run_ids": runs}),
        comparison_id=identity,
        expected_revision=revision,
    )
    with pytest.raises(ValueError, match="cursor"):
        await repo.read(scope, principal, identity, newer, cursor=first.body["next_cursor"])


async def test_retained_details_survive_source_history_cleanup_and_new_projection(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    await write(
        run,
        scope,
        2,
        {
            "kind": "tool",
            "status": "completed",
            "tool_name": "safe_tool",
            "activity_id": str(uuid4()),
        },
        kind="step",
        identity="fixed-step",
    )
    repo = repository(service)
    identity, revision = await repo.materialize(
        scope,
        principal,
        comparison_request({"mode": "explicit", "run_ids": [run], "detail_run_ids": [run]}),
    )
    before = await repo.read(scope, principal, identity, revision, detail_run_ids=[run])
    async with execution_admin_session() as db:
        await db.execute(text("DELETE FROM execution_view_steps WHERE run_id=:run"), {"run": run})
        await db.execute(
            text("DELETE FROM execution_view_observations WHERE run_id=:run"), {"run": run}
        )
        await db.execute(
            text(
                "UPDATE execution_view_runs SET projection_revision=projection_revision+100 WHERE run_id=:run"
            ),
            {"run": run},
        )
        await db.commit()
    after = await repo.read(scope, principal, identity, revision, detail_run_ids=[run])
    assert after.body == before.body


async def test_later_detail_selection_never_reads_latest_when_original_not_retained(datasets):
    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    repo, identity, revision = await capture(service, scope, principal, [run])
    await write(
        run,
        scope,
        2,
        {"kind": "tool", "status": "completed", "activity_id": str(uuid4())},
        kind="step",
        identity="late-step",
    )
    result = await repo.read(scope, principal, identity, revision, detail_run_ids=[run])
    assert "retained_data_unavailable" in str(result.body)
    assert "late-step" not in str(result.body)


async def test_removed_run_is_excluded_from_current_subset_and_metrics(datasets):
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    visible, hidden = await make_run(scope), await make_run(scope, status="failed")
    repo, identity, revision = await capture(service, scope, principal, [visible, hidden])
    async with execution_admin_session() as db:
        for table in (
            "execution_view_observations",
            "execution_view_checkpoints",
            "execution_view_steps",
        ):
            await db.execute(text(f"DELETE FROM {table} WHERE run_id=:run"), {"run": hidden})
        await db.execute(text("DELETE FROM execution_view_runs WHERE run_id=:run"), {"run": hidden})
        await db.commit()
    result = await repo.read(scope, principal, identity, revision)
    assert result.body["member_count"] == 1
    assert hidden not in str(result.body)
    assert [member["run_id"] for member in result.body["members"]] == [visible]


async def test_full_capacity_is_durable_and_cap_plus_one_refresh_rolls_back(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    async with execution_admin_session() as db:
        await db.execute(
            text("""INSERT INTO execution_view_runs(run_id,family,status,purpose,admitted_at,terminal_at,completeness,capabilities,projection_revision,projector_version,formal_position,progress_position,observed_order,owner_user_id,team_id,created_by)
          SELECT gen_random_uuid(),family,status,purpose,admitted_at,terminal_at,completeness,capabilities,projection_revision,projector_version,formal_position,progress_position,observed_order,owner_user_id,team_id,created_by FROM execution_view_runs CROSS JOIN generate_series(1,99999) WHERE run_id=:run"""),
            {"run": run},
        )
        await db.commit()
    repo, identity, revision = await capture(service, scope, principal)
    assert (await repo.read(scope, principal, identity, revision, limit=1)).body[
        "member_count"
    ] == 100000
    await make_run(scope)
    with pytest.raises(ValueError, match="comparison_capacity_exceeded"):
        await repo.materialize(
            scope,
            principal,
            comparison_request({"mode": "all_matching"}),
            comparison_id=identity,
            expected_revision=revision,
        )
    assert (await repo.read(scope, principal, identity, revision, limit=1)).body[
        "member_count"
    ] == 100000


async def seed_model_score(fixture, candidate, *, request_id):
    from app.domain.evaluation.scoring import ScoreValue
    from app.domain.models.authorization import AuthorizationContext

    suites, scope, principal, suite, *_ = fixture
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_score.append(
            scope,
            principal,
            candidate,
            source="model",
            scores=tuple(
                ScoreValue(
                    source="model",
                    dimension=dimension.id,
                    rubric_revision=rubric.id,
                    value=4,
                    status="valid",
                )
                for dimension in rubric.dimensions
            ),
            expected_evaluation_revision=0,
            request_id=request_id,
            required_dimensions=(),
            applicable_dimensions=tuple(dimension.id for dimension in rubric.dimensions),
        )
        await work.commit()


async def test_later_human_score_does_not_rewrite_retained_score_head(budget_binding_fixture):
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup

    reviews, scope, principal, _batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    await seed_model_score(budget_binding_fixture, candidate, request_id="comparison-initial-model")
    repo, identity, revision = await capture(
        reviews.suites, scope, principal, [str(candidate.run_id)]
    )
    before = await repo.read(scope, principal, identity, revision)
    payload = payload.model_copy(update={"expected_result_revision": candidate.result_revision + 1})
    await reviews.append_score(
        scope, principal, candidate.result_id, 1, "comparison-later-review", payload
    )
    after = await repo.read(scope, principal, identity, revision)
    assert before.body == after.body
    assert after.body["member_count"] == 1


async def test_safe_metrics_match_a01_for_identical_fixed_cohort(datasets):
    from dataclasses import replace

    from app.application.ports.execution_comparison import ComparisonRequest
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        captured_run,
    )

    service, scope, principal, *_ = datasets
    _analysis, query, original, run = await captured_run(datasets)
    repo = repository(service)
    request = ComparisonRequest(
        query, "explicit", (str(run),), (), (), None, str(uuid4()), "parity"
    )
    identity, revision = await repo.materialize(scope, principal, request)
    metrics = (await repo.read(scope, principal, identity, revision)).body["metrics"]
    for key in ("series", "intervals", "usage", "scores", "approvals", "allocations"):
        assert metrics[key] == original.metrics[key]


async def test_manual_alignment_cas_records_current_author_and_replaces_pair(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest

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
    assert (
        await repo.align(
            scope,
            principal,
            identity,
            revision,
            expected_revision=0,
            edits=[edit],
            request_id=str(uuid4()),
        )
        == 1
    )
    with pytest.raises(ValueError, match="alignment_conflict"):
        await repo.align(
            scope,
            principal,
            identity,
            revision,
            expected_revision=0,
            edits=[edit],
            request_id=str(uuid4()),
        )
    await repo.align(
        scope,
        principal,
        identity,
        revision,
        expected_revision=1,
        request_id=str(uuid4()),
        edits=[{**edit, "action": "unpair"}],
    )
    body = (await repo.read(scope, principal, identity, revision, detail_run_ids=runs)).body
    assert body["alignment_revision"] == 2
    assert len(body["alignments"]) == 1
    assert body["alignments"][0]["author"] == principal.user_id
    assert body["alignments"][0]["supersedes"] == 1
    assert body["alignments"][0]["edit"]["action"] == "unpair"


@pytest.mark.parametrize("mutation", ["file_unavailable", "pin_invalidated", "session_deleted"])
async def test_fixed_resource_bindings_recheck_partial_revocation(datasets, mutation):
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.resource_pin import ResourceIdentity
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    session, file_id = str(uuid4()), str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO sessions(id,owner_user_id) VALUES(:id,:owner)"),
            {"id": session, "owner": principal.user_id},
        )
        await db.execute(
            text(
                "INSERT INTO files(id,key,owner_user_id,content_digest,object_identity) VALUES(:id,:id,:owner,:digest,:object)"
            ),
            {"id": file_id, "owner": principal.user_id, "digest": "a" * 64, "object": str(uuid4())},
        )
        await db.commit()
    visible, hidden = await make_run(scope), await make_run(scope)
    await write(hidden, scope, 2, {"source": {"entity_type": "session", "entity_id": session}})
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        await work.resource_pins.acquire(
            scope,
            "run",
            hidden,
            [
                ResourceIdentity(
                    resource_kind="file", resource_id=file_id, resource_version="a" * 64
                )
            ],
        )
        await work.commit()
    repo, identity, revision = await capture(service, scope, principal, [visible, hidden])
    before = await repo.current(scope, principal, identity, revision)
    async with execution_admin_session() as db:
        statements = {
            "file_unavailable": "UPDATE files SET content_available=false WHERE id=:id",
            "pin_invalidated": "UPDATE resource_pins SET available=false,unavailable_reason='force_deleted',unavailable_at=clock_timestamp() WHERE resource_id=:id",
            "session_deleted": "UPDATE sessions SET deleted_at=clock_timestamp() WHERE id=:id",
        }
        await db.execute(
            text(statements[mutation]),
            {"id": session if mutation == "session_deleted" else file_id},
        )
        await db.commit()
    body = (await repo.read(scope, principal, identity, revision)).body
    assert body["member_count"] == 1
    assert hidden not in str(body)
    assert (await repo.current(scope, principal, identity, revision)) != before


async def test_physical_publication_cut_stays_fixed_until_explicit_refresh(datasets):
    from app.application.ports.execution_comparison import ComparisonRequest
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, *_ = datasets
    run = await make_run(scope)
    values = {
        "owner": principal.user_id,
        "run": run,
        "call": str(uuid4()),
        "config": str(uuid4()),
        "activity": uuid4(),
        "event": uuid4(),
    }
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO execution_configurations(id,run_id,body,purpose,owner_user_id,created_by) VALUES(:config,:run,'{}','evaluation_judge',:owner,:owner)"
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
                "INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,created_by) VALUES(:call,'dispatch',:event,1,:owner,:owner)"
            ),
            values,
        )
        await db.execute(
            text(
                "INSERT INTO execution_model_settlements(call_identity,fact,owner_user_id,created_by) VALUES(:call,CAST(:fact AS jsonb),:owner,:owner)"
            ),
            {
                **values,
                "fact": '{"usage":{"prompt_tokens":12,"completion_tokens":3},"cost_usd":"0.25"}',
            },
        )
        await db.execute(
            text(
                "INSERT INTO execution_usage_publications(call_identity,phase,event_id,event_position,owner_user_id,created_by) VALUES(:call,'settlement',:event,2,:owner,:owner)"
            ),
            {**values, "event": uuid4()},
        )
        await db.commit()
    repo, identity, revision = await capture(service, scope, principal, [run])
    before = (await repo.read(scope, principal, identity, revision)).body
    assert before["metrics"]["usage"]["purposes"]["evaluation_judge"]["cost_usd"]["value"] is None
    await write(run, scope, 2, {"status": "completed"})
    assert (await repo.read(scope, principal, identity, revision)).body == before
    _, refreshed = await repo.materialize(
        scope,
        principal,
        comparison_request({"mode": "explicit", "run_ids": [run]}),
        comparison_id=identity,
        expected_revision=revision,
    )
    after = (await repo.read(scope, principal, identity, refreshed)).body
    assert after["metrics"]["usage"]["purposes"]["evaluation_judge"]["cost_usd"]["value"] == "0.25"


async def fixed_artifact_comparison(datasets):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.application.services.artifact_service import ArtifactService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_artifact_provenance import (
        ArtifactProvenanceMaintenance,
    )
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from tests.app.application.services.test_artifact_provenance_postgres import (
        Objects,
        production_run,
        uow,
    )
    from tests.app.artifact_test_support import UnitOfWorkUploadIntents
    from tests.app.execution_test_support import (
        authenticated_session_factory,
        execution_admin_database_uri,
        execution_admin_session,
        execution_kernel_database_uri,
    )

    service = datasets[0]
    repo = repository(service)
    _, requested_scope, principal, *_ = datasets
    session, producer, command, handler = await production_run(scope=requested_scope)
    scope, run = producer.scope, str(producer.run_id)
    objects = Objects()
    artifact = await ArtifactService(
        uow, objects, upload_intents=UnitOfWorkUploadIntents(uow)
    ).write_content(session, None, "doc", "fixed", "fixed", producer=producer, verify_upload=False)
    await command("FailRun", {"failure_code": "comparison_fixture"})
    kernel_engine = create_async_engine(
        make_url(execution_kernel_database_uri()).set(
            database=make_url(execution_admin_database_uri()).database
        ),
        pool_size=1,
        max_overflow=0,
    )
    try:
        kernel_sessions = authenticated_session_factory(kernel_engine, signing_secret=repo.secret)
        projector = PostgresFormalProjector(
            session_factory=kernel_sessions,
            authorization=AuthorizationContext.system("execution-kernel"),
        )
        await projector.run_once(scope, limit=1000)
        maintenance = ArtifactProvenanceMaintenance(
            session_factory=kernel_sessions,
            authorization=AuthorizationContext.system("execution-kernel"),
            objects=objects,
            handler=handler,
        )
        await maintenance.process_pending()
        await projector.run_once(scope, limit=1000)
    finally:
        await kernel_engine.dispose()
    async with execution_admin_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT step_id,artifact_refs FROM execution_view_steps WHERE run_id=CAST(:run AS uuid)"
                ),
                {"run": run},
            )
        ).all()
        matching_steps = [
            step_id
            for step_id, refs in rows
            if any(ref["artifact_id"] == artifact.id and ref["version"] == 1 for ref in refs or [])
        ]
        assert len(matching_steps) == 1
        assert (
            await db.scalar(
                text("""SELECT count(*) FROM artifact_version_provenance
                WHERE artifact_id=:artifact AND version=1 AND binding_status='bound'"""),
                {"artifact": artifact.id},
            )
            == 1
        )
    step_id = matching_steps[0]
    identity, revision = await repo.materialize(
        scope,
        principal,
        comparison_request({"mode": "explicit", "run_ids": [run], "detail_run_ids": [run]}),
    )
    selection = {
        "left": {"run_id": run, "step_id": step_id, "artifact_id": artifact.id, "version": 1},
        "right": {"run_id": run, "step_id": step_id, "artifact_id": artifact.id, "version": 1},
        "format": "text",
    }
    return repo, scope, principal, identity, revision, selection


async def test_durable_job_claim_recovery_fences_stale_worker_and_pages_reauthorize(datasets):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_comparison_diff_jobs import DBComparisonDiffJobs
    from app.infrastructure.repositories.db_execution_comparison_repository import (
        DBExecutionComparisonRepository,
    )
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import execution_admin_session

    repo, scope, principal, identity, revision, selection = await fixed_artifact_comparison(
        datasets
    )
    jobs = DBComparisonDiffJobs(repo)
    queued = await jobs.enqueue(
        scope, principal, identity, revision, selection, request_id="diff-command"
    )
    assert (
        await jobs.enqueue(
            scope, principal, identity, revision, selection, request_id="diff-command"
        )
        == queued
    )
    # The owned DDL test connection executes the kernel-only signed claim function.
    kernel = DBComparisonDiffJobs(
        DBExecutionComparisonRepository(execution_admin_session, signing_secret=repo.secret)
    )
    first = await kernel.claim()
    assert first["id"] == queued["job_id"]
    assert await kernel.claim() is None
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db, AuthorizationContext.system("execution-kernel"), signing_secret=repo.secret
        )
        await db.execute(
            text(
                "UPDATE comparison_diff_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": first["id"]},
        )
        await db.commit()
    second = await kernel.claim()
    assert second["lease_token"] != first["lease_token"]
    result = {
        "left": {},
        "right": {},
        "format": "text",
        "diff": {
            "content_changed": True,
            "complete": True,
            "reason": None,
            "content": "界" * 30000,
            "operations": [],
        },
    }
    with pytest.raises(ValueError, match="lease_lost"):
        await jobs.publish(first, scope, principal, result)
    await jobs.publish(second, scope, principal, result)
    page = await jobs.page(scope, principal, first["id"])
    assert page["status"] == "complete"
    assert page["next_cursor"]
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE artifact_version_provenance SET availability='unavailable' WHERE artifact_id=:id"
            ),
            {"id": selection["left"]["artifact_id"]},
        )
        await db.commit()
    with pytest.raises(ValueError, match="artifact_unavailable"):
        await jobs.page(scope, principal, first["id"], cursor=page["next_cursor"])


@pytest.mark.parametrize("evidence", ["consumed", "missing", "contract_mismatch"])
async def test_actual_recording_ledger_materializes_only_compatible_activity_evidence(
    budget_binding_fixture, evidence
):
    import json

    from app.application.ports.execution_comparison import ComparisonRequest
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

    suites, scope, principal, *_ = budget_binding_fixture
    _, _, _batch, candidate = await completed(budget_binding_fixture)
    repo = repository(suites)
    run = str(candidate.run_id)
    async with execution_admin_session() as db:
        version, job = (
            await db.execute(
                text("""SELECT b.version_id,v.job_id FROM evaluation_replay_bindings b
                    JOIN evaluation_recording_versions v ON v.id=b.version_id AND v.scope_key=b.scope_key
                    WHERE b.run_id=:run"""),
                {"run": run},
            )
        ).one()
    obj, slot, activity = [uuid4() for _ in range(3)]
    key, contract = "a" * 64, "b" * 64
    values = {
        "owner": principal.user_id,
        "run": run,
        "job": job,
        "version": version,
        "object": obj,
        "storage": str(obj),
        "slot": slot,
        "activity": activity,
        "key": key,
        "principal": json.dumps(principal.model_dump(mode="json")),
        "body": json.dumps(
            {"tool": "recorded_tool", "contract_digest": contract, "match_key": key}
        ),
    }
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=repo.secret,
        )
        for statement in (
            "INSERT INTO evaluation_recording_objects(id,job_id,storage_key,digest,size_bytes,owner_user_id,created_by) VALUES(:object,:job,:storage,:key,1,:owner,:owner)",
            "INSERT INTO evaluation_recording_slots(id,version_id,match_key,object_id,body,owner_user_id,created_by) VALUES(:slot,:version,:key,:object,CAST(:body AS jsonb),:owner,:owner)",
        ):
            await db.execute(text(statement), values)
        if evidence != "missing":
            await db.execute(
                text(
                    "INSERT INTO evaluation_replay_ledger(run_id,activity_id,version_id,slot_id,match_key,owner_user_id,created_by) VALUES(:run,:activity,:version,:slot,:key,:owner,:owner)"
                ),
                values,
            )
        await db.commit()
    await write(
        run,
        scope,
        1000,
        {
            "kind": "tool",
            "status": "completed",
            "activity_id": str(activity),
            "attempt_id": "later-attempt",
            "tool_name": "recorded_tool",
        },
        kind="step",
        identity="recorded",
    )
    # Nullable public compatibility fields have no producer. A non-null incompatible
    # historical value must suppress the ledger suggestion, never become authority.
    if evidence == "contract_mismatch":
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "UPDATE execution_view_steps SET tool_contract_revision='different' WHERE run_id=:run AND step_id='recorded'"
                ),
                {"run": run},
            )
            await db.commit()
    await seed_model_score(budget_binding_fixture, candidate, request_id="comparison-ledger-model")
    identity, revision = await repo.materialize(
        scope,
        principal,
        comparison_request({"mode": "explicit", "run_ids": [run], "detail_run_ids": [run]}),
    )
    detail = (await repo.read(scope, principal, identity, revision, detail_run_ids=[run])).body[
        "details"
    ][0]["body"]
    proof = detail["semantic_evidence"]
    if evidence == "consumed":
        assert len(proof) == 1
        assert proof[0]["provenance_scope"] == "activity"
        assert proof[0]["attempt_id"] == "later-attempt"
        assert proof[0]["tool_contract_revision"] == contract
    else:
        assert proof == []


async def test_comparison_survives_cleanup_of_a01_capture(datasets):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        captured_run,
    )

    service, scope, principal, *_ = datasets
    _analysis, _query, source_capture, run = await captured_run(datasets)
    repo, identity, revision = await capture(service, scope, principal, [str(run)])
    before = (await repo.read(scope, principal, identity, revision)).body
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.system("execution-kernel"),
            signing_secret=repo.secret,
        )
        removed = await db.execute(
            text("DELETE FROM analysis_captures WHERE id=CAST(:id AS uuid)"),
            {"id": source_capture.watermark},
        )
        assert removed.rowcount == 1
        await db.commit()
    assert (await repo.read(scope, principal, identity, revision)).body == before
