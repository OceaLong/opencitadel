"""Strict PostgreSQL definitions; collect only while the SQL runtime gate is deferred.

The fixture seeds a captured cost dependency to isolate retained-resource/current-owner
semantics independently of evaluator dispatch. It does not claim capture producer coverage.
"""

# ruff: noqa: F401,F811
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_pin import ResourceIdentity, ResourcePinned
from app.infrastructure.repositories.db_analysis_points import points_operation
from app.infrastructure.repositories.db_execution_comparison_repository import (
    comparison_owner_validator,
)
from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.domain.analysis.test_point_series import record
from tests.app.execution_test_support import execution_admin_session
from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
    budget_binding_fixture,
    comparison_request,
    configurations,
    datasets,
    fixed_artifact_comparison,
    fresh_f07_database,
    isolated_database,
    make_run,
)

pytestmark = pytest.mark.asyncio


async def seed_subject_physical_fact(scope, principal, run_id):
    """Publish a settled source call without writing an evaluation summary fact."""
    values = {
        "run": str(run_id),
        "owner": principal.user_id,
        "call": str(uuid4()),
        "config": str(uuid4()),
        "activity": uuid4(),
        "fact": json.dumps(
            {"usage": {"prompt_tokens": 12, "completion_tokens": 3}, "cost_usd": "0.25"}
        ),
    }
    async with execution_admin_session() as db:
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


async def test_nonmember_cost_contributor_pins_protect_cleanup_and_revoke_only_cost(datasets):
    repo, scope, principal, other_id, other_revision, selection = await fixed_artifact_comparison(
        datasets
    )
    contributor = selection["left"]["run_id"]
    artifact = selection["left"]["artifact_id"]
    main = await make_run(scope)
    identity, revision = await repo.materialize(
        scope, principal, comparison_request({"mode": "explicit", "run_ids": [main]})
    )
    saved = record()
    saved["row"]["run_id"] = main
    saved["row"]["attempts"] = [
        {"attempt": 1, "run_id": contributor, "run_revision": 1, "status": "intent"}
    ]
    saved["row"]["score_run_id"] = contributor
    saved["row"]["score_run_revision"] = 1
    saved["row"]["score_result_revision"] = 1
    saved["primary_run_id"] = main
    result = saved["row"]["id"]
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=repo.secret,
        )
        capture = await db.scalar(
            text(
                "SELECT id FROM comparison_revisions WHERE comparison_id=:id AND revision=:revision"
            ),
            {"id": identity, "revision": revision},
        )
        source = await db.scalar(
            text(
                "SELECT id FROM comparison_revisions WHERE comparison_id=:id AND revision=:revision"
            ),
            {"id": other_id, "revision": other_revision},
        )
        scope_key = "team:" + scope.team_id if scope.team_id else "user:" + principal.user_id
        await db.execute(
            text(
                "INSERT INTO comparison_resources(capture_id,scope_key,run_id,source,resources,pins,owners) SELECT :capture,scope_key,run_id,source,resources,pins,owners FROM comparison_resources WHERE capture_id=:source AND run_id=:run"
            ),
            {"capture": capture, "source": source, "run": contributor},
        )
        await db.execute(
            text(
                "INSERT INTO analysis_point_dependencies(capture_kind,capture_id,scope_key,result_id,run_id,cut,manifest,captured_available) VALUES('comparison',:capture,:scope,:result,:run,'{}','true',true)"
            ),
            {"capture": capture, "scope": scope_key, "result": result, "run": contributor},
        )
        await db.execute(
            text(
                "INSERT INTO analysis_point_rows(capture_kind,capture_id,scope_key,run_id,result_id,body) VALUES('comparison',:capture,:scope,:run,:result,CAST(:body AS jsonb))"
            ),
            {
                "capture": capture,
                "scope": scope_key,
                "run": main,
                "result": result,
                "body": json.dumps(saved),
            },
        )
        assert not await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM comparison_members WHERE capture_id=:capture AND run_id=:run)"
            ),
            {"capture": capture, "run": contributor},
        )
        await db.commit()
    resource = ResourceIdentity(
        resource_kind="artifact", resource_id=artifact, resource_version="1"
    )
    async with repo.transactions.transaction(scope, principal) as db:
        pins = DBResourcePinRepository(
            db,
            owner_validators={
                "comparison_revision": comparison_owner_validator(
                    principal, signing_secret=repo.secret
                )
            },
        )
        await pins.acquire(scope, "comparison_revision", str(capture), [resource])
        # Remove the original comparison pin: protection must come from this owner.
        await pins.release(scope, "comparison_revision", str(source), [resource])
        with pytest.raises(ResourcePinned):
            await pins.guard_delete("artifact", artifact)
        current = await points_operation(
            db,
            scope,
            principal,
            secret=repo.secret,
            kind="comparison",
            capture=str(capture),
            operation="read",
        )
        assert current["records"][0]["row"]["subject_usage"]["money"] == "0.20"
        assert current["records"][0]["row"]["attempts"][0]["run_id"] == contributor
        assert current["records"][0]["row"]["score_run_id"] == contributor
    before = (await repo.read(scope, principal, identity, revision)).body
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE sessions SET deleted_at=clock_timestamp() WHERE id=(SELECT session_id FROM artifacts WHERE id=:id)"
            ),
            {"id": artifact},
        )
        await db.commit()
    async with repo.transactions.transaction(scope, principal) as db:
        denied = await points_operation(
            db,
            scope,
            principal,
            secret=repo.secret,
            kind="comparison",
            capture=str(capture),
            operation="read",
        )
        assert denied["records"][0]["row"]["value"] == 4
        assert denied["records"][0]["row"]["subject_usage"] is None
        assert denied["records"][0]["row"]["judge_usage"] is None
        assert denied["records"][0]["row"]["attempts"] == []
        assert denied["records"][0]["row"]["score_run_id"] is None
        assert denied["records"][0]["row"]["score_run_revision"] is None
        assert denied["records"][0]["row"]["score_result_revision"] is None
        assert denied["fingerprint"] != current["fingerprint"]
    after = (await repo.read(scope, principal, identity, revision)).body
    assert after["member_count"] == before["member_count"] == 1
    assert after["members"][0]["run_id"] == main
    assert after["metrics"]["series"] == before["metrics"]["series"]


async def test_real_analysis_comparison_producers_keep_points_rows_and_refresh_revision(
    budget_binding_fixture, datasets
):
    """No retained-fact inserts: public constructors -> actual0021/0022 producers."""
    from app.application.ports.execution_analysis import AnalysisQuery
    from app.domain.evaluation.review import HumanScore
    from app.domain.evaluation.summary import EvaluationSnapshot
    from app.domain.evaluation.summary_metrics import derive_snapshot
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        analysis_repository,
    )
    from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
        repository,
    )

    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    first = await service.append_score(
        scope, principal, candidate.result_id, 0, "a04-fixed-score", payload
    )
    query = AnalysisQuery.parse({"batch_id": str(batch.id)}, "day", "UTC")
    analysis = analysis_repository(datasets[0])
    capture = await analysis.capture(scope, principal, query, None)
    page = await analysis.run_page(scope, principal, capture, limit=1)
    assert page["availability"] == "available"
    assert page["items"][0]["run_id"] == str(candidate.run_id)
    assert page["watermark"] == capture.watermark
    series = next(
        item
        for item in capture.metrics["evaluation_series"]
        if item["source"] == "human" and item["dimension"] == "correctness"
    )
    assert series["points"][0]["value"] == 3
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        e11 = await work.evaluation_summary.capture(
            scope,
            principal,
            batch.id,
            source="human",
            dimension="correctness",
            rubric_id=payload.rubric_version,
            evaluation_revision=first.evaluation_revision,
        )
        assert series["points"] == [
            p.model_dump(mode="json")
            for p in derive_snapshot(EvaluationSnapshot.model_validate(e11))["points"]
        ]
        await work.commit()
    comparison = repository(datasets[0])
    request = comparison_request(
        {
            "mode": "explicit",
            "run_ids": [str(candidate.run_id)],
            "filters": {"batch_id": str(batch.id)},
            "timezone": "UTC",
        }
    )
    identity, revision = await comparison.materialize(scope, principal, request)
    old = (await comparison.read(scope, principal, identity, revision)).body
    assert old["context"]["timezone"] == "UTC"
    assert old["members"][0]["run_id"] == page["items"][0]["run_id"]
    assert (
        next(
            x
            for x in old["metrics"]["evaluation_series"]
            if x["source"] == "human" and x["dimension"] == "correctness"
        )["points"]
        == series["points"]
    )
    # Append through the real review API; fixed accepted rows must not join latest heads.
    await service.append_score(
        scope,
        principal,
        candidate.result_id,
        first.evaluation_revision,
        "a04-later-score",
        payload.model_copy(
            update={
                "expected_result_revision": first.result_revision,
                "scores": (HumanScore(dimension="correctness", value=1, reason="later"),),
            }
        ),
    )
    replay = await analysis.capture(scope, principal, query, capture.watermark)
    assert replay.metrics["evaluation_series"] == capture.metrics["evaluation_series"]
    assert (await comparison.read(scope, principal, identity, revision)).body["metrics"][
        "evaluation_series"
    ] == old["metrics"]["evaluation_series"]
    next_request = comparison_request(
        {
            "mode": "explicit",
            "run_ids": [str(candidate.run_id)],
            "filters": {"batch_id": str(batch.id)},
            "timezone": "UTC",
        }
    )
    same, newer = await comparison.materialize(
        scope, principal, next_request, comparison_id=identity, expected_revision=revision
    )
    assert same == identity
    assert newer == revision + 1
    fresh = (await comparison.read(scope, principal, identity, newer)).body
    assert (
        next(
            x
            for x in fresh["metrics"]["evaluation_series"]
            if x["source"] == "human" and x["dimension"] == "correctness"
        )["points"][0]["value"]
        == 1
    )


async def test_unscored_unresolved_physical_call_does_not_publish_points(
    budget_binding_fixture, datasets
):
    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        late_physical_case,
    )
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        analysis_repository,
    )
    from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
        repository,
    )

    _, _, batch, result, _, _ = await late_physical_case(budget_binding_fixture, "succeeded")
    _, scope, principal, *_ = budget_binding_fixture
    analysis = analysis_repository(datasets[0])
    query = AnalysisQuery.parse({"batch_id": str(batch.id)}, "day", "UTC")
    comparison = repository(datasets[0])
    request = comparison_request(
        {
            "mode": "explicit",
            "run_ids": [str(result["run_id"])],
            "filters": {"batch_id": str(batch.id)},
        }
    )
    with pytest.raises(ValueError, match="analysis_applicability_unavailable"):
        await analysis.capture(scope, principal, query, None)
    with pytest.raises(ValueError, match="analysis_applicability_unavailable"):
        await comparison.materialize(scope, principal, request)


async def test_pending_judge_later_projection_never_backfills_an_accepted_cut(
    budget_binding_fixture, datasets
):
    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.execution.test_postgres_execution_view import write
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        analysis_repository,
    )
    from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
        repository,
    )

    review, scope, principal, batch, candidate, payload = await review_setup(budget_binding_fixture)
    receipt = await review.append_score(
        scope, principal, candidate.result_id, 0, "a04-pending-judge-score", payload
    )
    candidate = candidate.model_copy(update={"result_revision": receipt.result_revision})
    suites, _, _, suite, _, _, factory = budget_binding_fixture
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.create(
            scope,
            principal,
            candidate,
            rubric_id=rubric.id,
            config_id=rubric.judge_config_version,
            request_id="a04-pending-judge",
            materials={"rubric": [], "evidence": {}, "unavailable": {}},
            namespace_id=batch.id,
        )
        await work.commit()
    judge, subject = intent["run_id"], str(candidate.run_id)
    analysis = analysis_repository(datasets[0])
    query = AnalysisQuery.parse({"batch_id": str(batch.id)}, "day", "UTC")
    capture = await analysis.capture(scope, principal, query, None)
    before_authority = await analysis.current(scope, principal, capture)
    comparison = repository(datasets[0])
    identity, revision = await comparison.materialize(
        scope,
        principal,
        comparison_request(
            {"mode": "explicit", "run_ids": [subject], "filters": {"batch_id": str(batch.id)}}
        ),
    )
    before = (await comparison.read(scope, principal, identity, revision)).body["metrics"][
        "evaluation_series"
    ]
    assert before
    assert all(p["cost_usd"] is None for group in before for p in group["points"])
    # The standard projection writer materializes the already accepted intent later.
    await write(
        judge, scope, 1, {"family": "ask", "purpose": "evaluation_judge", "status": "running"}
    )
    assert await analysis.current(scope, principal, capture) == before_authority
    assert (await comparison.read(scope, principal, identity, revision)).body["metrics"][
        "evaluation_series"
    ] == before
    assert (
        await analysis.capture(scope, principal, query, capture.watermark)
    ).metrics == capture.metrics


async def test_real_capture_retains_nonmember_judge_resource_and_denies_cost_after_revoke(
    budget_binding_fixture, datasets
):
    from app.application.ports.execution_analysis import AnalysisQuery
    from tests.app.infrastructure.execution.test_postgres_execution_view import write
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup
    from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
        analysis_repository,
    )
    from tests.app.infrastructure.repositories.test_execution_comparison_repository import (
        repository,
    )

    _, _, _, _, _, artifact_selection = await fixed_artifact_comparison(datasets)
    artifact = artifact_selection["left"]["artifact_id"]
    review, scope, principal, batch, candidate, payload = await review_setup(budget_binding_fixture)
    receipt = await review.append_score(
        scope, principal, candidate.result_id, 0, "a04-judge-dependency", payload
    )
    candidate = candidate.model_copy(update={"result_revision": receipt.result_revision})
    suites, _, _, suite, _, _, factory = budget_binding_fixture
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.create(
            scope,
            principal,
            candidate,
            rubric_id=rubric.id,
            config_id=rubric.judge_config_version,
            request_id="a04-contributor",
            materials={"rubric": [], "evidence": {}, "unavailable": {}},
            namespace_id=batch.id,
        )
        await work.commit()
    judge = str(intent["run_id"])
    # Standard projection fixture, never a direct retained-fact insertion.
    await write(
        judge, scope, 1, {"family": "ask", "purpose": "evaluation_judge", "status": "completed"}
    )
    await seed_subject_physical_fact(scope, principal, candidate.run_id)
    comparison = repository(datasets[0])
    resource = ResourceIdentity(
        resource_kind="artifact", resource_id=artifact, resource_version="1"
    )
    async with comparison.transactions.transaction(scope, principal) as db:
        await DBResourcePinRepository(db).acquire(scope, "run", judge, [resource])
    analysis = analysis_repository(datasets[0])
    query = AnalysisQuery.parse({"batch_id": str(batch.id)}, "day", "UTC")
    captured = await analysis.capture(scope, principal, query, None)
    previous_authority = await analysis.current(scope, principal, captured)
    identity, revision = await comparison.materialize(
        scope,
        principal,
        comparison_request(
            {
                "mode": "explicit",
                "run_ids": [str(candidate.run_id)],
                "filters": {"batch_id": str(batch.id)},
            }
        ),
    )
    before = (await comparison.read(scope, principal, identity, revision)).body
    human = next(
        group
        for group in before["metrics"]["evaluation_series"]
        if group["source"] == "human" and group["dimension"] == "correctness"
    )
    assert human["points"][0]["value"] == 3
    assert human["rows"][0]["subject_usage"] is not None
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=comparison.secret,
        )
        capture_id = await db.scalar(
            text(
                "SELECT id FROM comparison_revisions WHERE comparison_id=:id AND revision=:revision"
            ),
            {"id": identity, "revision": revision},
        )
        assert not await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM comparison_members WHERE capture_id=:capture AND run_id=:run)"
            ),
            {"capture": capture_id, "run": judge},
        )
        assert await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM analysis_point_dependencies WHERE capture_kind='comparison' AND capture_id=:capture AND run_id=:run AND captured_available)"
            ),
            {"capture": capture_id, "run": judge},
        )
        assert await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM resource_pins WHERE owner_kind='comparison_revision' AND owner_id=:capture AND resource_kind='artifact' AND resource_id=:artifact AND available)"
            ),
            {"capture": str(capture_id), "artifact": artifact},
        )
    async with comparison.transactions.transaction(scope, principal) as db:
        with pytest.raises(ResourcePinned):
            await DBResourcePinRepository(db).guard_delete("artifact", artifact)
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE sessions SET deleted_at=clock_timestamp() WHERE id=(SELECT session_id FROM artifacts WHERE id=:id)"
            ),
            {"id": artifact},
        )
        await db.commit()
    assert await analysis.current(scope, principal, captured) != previous_authority
    after = (await comparison.read(scope, principal, identity, revision)).body
    human = next(
        group
        for group in after["metrics"]["evaluation_series"]
        if group["source"] == "human" and group["dimension"] == "correctness"
    )
    assert human["points"][0]["value"] == 3
    assert human["points"][0]["cost_usd"] is None
    assert human["rows"][0]["subject_usage"] is None
    assert after["member_count"] == before["member_count"] == 1
    assert after["metrics"]["series"] == before["metrics"]["series"]
