"""Append/CAS/audit in one borrowed transaction; no body I/O while fenced."""

import json
from uuid import UUID, uuid4

from sqlalchemy import text

from app.domain.evaluation.configuration import digest
from app.domain.evaluation.scoring import (
    ScoreRevision,
    ScoreValue,
    ScoringProjectionAdvanced,
    rule_settlement,
)
from app.domain.models.audit_log import AuditLog
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


class DBEvaluationScoreRepository:
    def __init__(self, work):
        self.work, self.db = work, work.db_session

    async def revision(self, scope, batch_id):
        return (
            await self.db.scalar(
                text(
                    "SELECT revision FROM evaluation_score_heads WHERE scope_key=:scope AND batch_id=:batch"
                ),
                params(scope, batch=batch_id),
            )
            or 0
        )

    async def settled(self, scope, result_id, source):
        return await self.db.scalar(
            text(
                "SELECT status FROM evaluation_score_sets WHERE scope_key=:scope AND result_id=:result AND source=:source ORDER BY evaluation_revision DESC LIMIT 1"
            ),
            params(scope, result=result_id, source=source),
        )

    async def eligible(self, scope, principal, candidate, *, lock=False, original_requester=True):
        repo = self.work.evaluation_batch
        if lock:
            await repo.lock(scope, candidate.batch_id)
        batch = await repo.get(scope, candidate.batch_id)
        if original_requester and batch["principal"] != principal.model_dump(mode="json"):
            raise PermissionError("scoring_original_requester_required")
        await self.work.evaluation_dataset.authorize(scope, principal, write=True)
        row = (
            (
                await self.db.execute(
                    text(
                        """SELECT r.*,a.run_id,a.command_id,a.run_revision FROM evaluation_batch_results r
          JOIN evaluation_batch_attempts a ON a.scope_key=r.scope_key AND a.result_id=r.id AND a.attempt=r.attempt
          WHERE r.scope_key=:scope AND r.id=:id AND r.batch_id=:batch"""
                        + (" FOR UPDATE OF r" if lock else "")
                    ),
                    params(scope, id=candidate.result_id, batch=candidate.batch_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["revision"] != candidate.result_revision
            or row["run_id"] != candidate.run_id
            or row["case_revision_id"] != candidate.case_revision_id
            or row["config_version_id"] != candidate.config_version_id
            or batch["suite_version"] != candidate.suite_version_id
            or row["execution_status"] != "succeeded"
            or row["unknown_effect"]
            or batch["cancel_requested"]
            or batch["status"]
            not in {"queued", "running", "waiting", "completed", "completed_with_errors"}
            or await repo.cancellation_requested(scope, candidate.batch_id)
        ):
            raise ValueError("scoring_candidate_stale")
        projection = await repo.projection(scope, row)
        receipt = await repo.receipt(scope, row)
        if (
            not projection
            or not projection["terminal"]
            or projection["status"] != "completed"
            or not receipt
            or receipt["status"] != "accepted"
        ):
            raise ValueError("scoring_execution_unavailable")
        revision_changed = projection["stream_version"] != candidate.run_revision
        # Batch/slot before namespace; namespace serializes physical admission/settlement.
        namespace = await self.db.scalar(
            text(
                "SELECT n.state FROM evaluation_budget_namespaces n JOIN evaluation_budget_bindings b ON b.scope_key=n.scope_key AND b.namespace_id=n.id WHERE b.scope_key=:scope AND b.run_id=:run"
                + (" FOR UPDATE OF n" if lock else "")
            ),
            params(scope, run=candidate.run_id),
        )
        if namespace is None or await repo.unknown_effect(
            scope, candidate.run_id, include_unresolved=True
        ):
            raise ValueError(
                "scoring_execution_unavailable" if revision_changed else "scoring_effect_unresolved"
            )
        if lock:
            await self.db.execute(
                text("SELECT id FROM users WHERE id=:id FOR SHARE"), {"id": principal.user_id}
            )
            if scope.team_id:
                await self.db.execute(
                    text(
                        "SELECT user_id FROM team_members WHERE user_id=:id AND team_id=:team FOR SHARE"
                    ),
                    {"id": principal.user_id, "team": scope.team_id},
                )
            await self.work.evaluation_dataset.authorize(scope, principal, write=True)
        if revision_changed:
            # Public stale candidates remain rejected. Only automatic consumers
            # may defer this precise race after all other authority, receipt,
            # terminal-state and effect checks have passed.
            error = (
                ScoringProjectionAdvanced
                if projection["stream_version"] > candidate.run_revision
                else ValueError
            )
            raise error("scoring_execution_unavailable")
        return dict(projection)

    async def append(
        self,
        scope,
        principal,
        candidate,
        *,
        source,
        scores,
        expected_evaluation_revision,
        request_id,
        required_dimensions,
        applicable_dimensions,
        supersedes=None,
        judge_run_id=None,
    ):
        if (
            source not in {"rule", "model", "human"}
            or not request_id.strip()
            or len(request_id) > 255
        ):
            raise ValueError("invalid_score_request")
        scores = tuple(ScoreValue.model_validate(s) for s in scores)
        dimensions = tuple(s.dimension for s in scores)
        if len(set(dimensions)) != len(dimensions) or any(
            s.source != source or s.rubric_revision is None for s in scores
        ):
            raise ValueError("invalid_score_dimensions")
        if not set(required_dimensions) <= set(dimensions) or len(
            set(applicable_dimensions)
        ) != len(applicable_dimensions):
            raise ValueError("invalid_score_applicability")
        supersedes = supersedes or {}
        if set(supersedes) - set(dimensions):
            raise ValueError("invalid_supersedes")
        fp = digest(
            {
                "candidate": candidate.model_dump(mode="json"),
                "source": source,
                "scores": [s.model_dump(mode="json") for s in scores],
                "required": list(required_dimensions),
                "applicable": list(applicable_dimensions),
                "supersedes": {k: str(v) for k, v in supersedes.items()},
            }
        )
        async with self.db.begin_nested():
            await self.work.evaluation_batch.lock(scope, candidate.batch_id)
            await self.work.evaluation_dataset.authorize(scope, principal, write=True)
            batch = await self.work.evaluation_batch.get(scope, candidate.batch_id)
            if batch["principal"] != principal.model_dump(mode="json") and source != "human":
                raise PermissionError("scoring_original_requester_required")
            prior = (
                (
                    await self.db.execute(
                        text(
                            "SELECT fingerprint,evaluation_revision FROM evaluation_score_sets WHERE scope_key=:scope AND result_id=:result AND source=:source AND request_id=:request"
                        ),
                        params(
                            scope, result=candidate.result_id, source=source, request=request_id
                        ),
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["fingerprint"] != fp:
                    raise ValueError("score_request_conflict")
                return prior["evaluation_revision"]
            await self.eligible(
                scope, principal, candidate, lock=True, original_requester=source != "human"
            )
            for resource in sorted(
                {e for s in scores for e in s.evidence},
                key=lambda e: (e.resource_kind, e.resource_id, e.resource_version),
            ):
                await self.work.resource_pins.resolve(scope, resource, lock=True)
            recordings = {s.recording for s in scores if s.recording is not None}
            for recording in recordings:
                from app.application.evaluation.recording_authority import validate_recording

                binding = await self.work.evaluation_recording.binding(scope, candidate.run_id)
                if binding is None or binding["version_id"] != recording.version_id:
                    raise ValueError("score_recording_changed")
                manifest = await validate_recording(
                    self.work, scope, principal, recording.version_id
                )
                coverage = await self.work.evaluation_recording.coverage(scope, candidate.run_id)
                if manifest.revision != recording.revision or any(
                    coverage[k] != getattr(recording, k)
                    for k in ("total", "consumed", "mismatches")
                ):
                    raise ValueError("score_recording_changed")
            suite = await self.work.evaluation_configuration.get_version(
                scope, "suite", candidate.suite_version_id
            )
            rubric_id = UUID(suite["rubric_version"])
            if judge_run_id is not None:
                intent = await self.work.evaluation_judge.get(scope, judge_run_id)
                if (
                    source != "model"
                    or intent is None
                    or intent["status"] != "submitted"
                    or intent["result_id"] != candidate.result_id
                    or intent["candidate"]["run_id"] != str(candidate.run_id)
                    or request_id != "judge:" + str(intent["id"])
                ):
                    raise ValueError("judge_score_binding_mismatch")
                await self.work.evaluation_judge.current(scope, intent)
                if await self.work.evaluation_judge.unsafe(scope, intent, include_unresolved=False):
                    projection = await self.work.evaluation_judge.projection(scope, intent)
                    if (
                        any(s.status != "error" or s.value is not None for s in scores)
                        or not projection
                        or not projection["terminal"]
                        or projection["status"] != "failed"
                        or projection["state"].get("failure_code")
                        != "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                    ):
                        raise ValueError("judge_effect_unknown")
                rubric_id = intent["rubric_id"]
            rubric = await self.work.evaluation_configuration.get_version(
                scope, "rubric", rubric_id
            )
            if (
                any(s.rubric_revision != rubric_id for s in scores)
                or not applicable_dimensions
                or not set(applicable_dimensions) <= {d["id"] for d in rubric["dimensions"]}
                or (source != "rule" and set(dimensions) != set(applicable_dimensions))
            ):
                raise ValueError("score_rubric_mismatch")
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_score_heads(batch_id,revision,owner_user_id,team_id,created_by) VALUES(:batch,0,:owner,:team,:actor) ON CONFLICT DO NOTHING"
                ),
                params(scope, batch=candidate.batch_id),
            )
            revision = await self.db.scalar(
                text(
                    "UPDATE evaluation_score_heads SET revision=revision+1 WHERE scope_key=:scope AND batch_id=:batch AND revision=:expected RETURNING revision"
                ),
                params(scope, batch=candidate.batch_id, expected=expected_evaluation_revision),
            )
            if revision is None:
                raise ValueError("evaluation_revision_conflict")
            set_id = uuid4()
            settlement = rule_settlement(scores)
            await self.db.execute(
                text("""INSERT INTO evaluation_score_sets(id,batch_id,result_id,result_revision,run_id,run_revision,evaluation_revision,rubric_revision,source,request_id,fingerprint,status,required_dimensions,applicable_dimensions,owner_user_id,team_id,created_by)
                VALUES(:id,:batch,:result,:result_revision,:run,:run_revision,:revision,:rubric,:source,:request,:fp,:status,CAST(:required AS jsonb),CAST(:applicable AS jsonb),:owner,:team,:actor)"""),
                params(
                    scope,
                    id=set_id,
                    batch=candidate.batch_id,
                    result=candidate.result_id,
                    result_revision=candidate.result_revision,
                    run=candidate.run_id,
                    run_revision=candidate.run_revision,
                    revision=revision,
                    rubric=rubric_id,
                    source=source,
                    request=request_id,
                    fp=fp,
                    status=settlement,
                    required=json.dumps(list(required_dimensions)),
                    applicable=json.dumps(list(applicable_dimensions)),
                ),
            )
            for score in scores:
                head = await self.db.scalar(
                    text(
                        """SELECT s.id FROM evaluation_scores s JOIN evaluation_score_sets v ON v.scope_key=s.scope_key AND v.id=s.set_id WHERE v.scope_key=:scope AND v.result_id=:result AND v.source=:source AND v.rubric_revision=:rubric AND s.dimension=:dimension ORDER BY v.evaluation_revision DESC LIMIT 1"""
                    ),
                    params(
                        scope,
                        result=candidate.result_id,
                        source=source,
                        dimension=score.dimension,
                        rubric=rubric_id,
                    ),
                )
                predecessor = supersedes.get(score.dimension, head)
                if predecessor is not None:
                    found = await self.db.scalar(
                        text(
                            "SELECT 1 FROM evaluation_scores s JOIN evaluation_score_sets v ON v.scope_key=s.scope_key AND v.id=s.set_id WHERE s.scope_key=:scope AND s.id=:id AND v.result_id=:result AND v.rubric_revision=:rubric AND v.source=:source AND s.dimension=:dimension"
                        ),
                        params(
                            scope,
                            id=predecessor,
                            source=source,
                            result=candidate.result_id,
                            rubric=rubric_id,
                            dimension=score.dimension,
                        ),
                    )
                    if not found:
                        raise ValueError("score_supersedes_mismatch")
                await self.db.execute(
                    text(
                        "INSERT INTO evaluation_scores(id,set_id,dimension,status,value,reason,evidence,recording,supersedes_id,owner_user_id,team_id,created_by) VALUES(:id,:set,:dimension,:status,CAST(:value AS jsonb),:reason,CAST(:evidence AS jsonb),CAST(:recording AS jsonb),:supersedes,:owner,:team,:actor)"
                    ),
                    params(
                        scope,
                        id=uuid4(),
                        set=set_id,
                        dimension=score.dimension,
                        status=score.status,
                        value=json.dumps(score.value) if score.value is not None else None,
                        reason=score.reason,
                        evidence=json.dumps([e.model_dump(mode="json") for e in score.evidence]),
                        recording=score.recording.model_dump_json() if score.recording else None,
                        supersedes=predecessor,
                    ),
                )
            if source == "model":
                await self.work.evaluation_review.model_rubric(
                    scope, candidate, rubric_id, rubric, applicable_dimensions, revision
                )
            rule_state = await self.settled(scope, candidate.result_id, "rule")
            model_state = await self.settled(scope, candidate.result_id, "model")
            # Every currently published rubric configures a judge. Thresholds do not disable it.
            overall = (
                "pending"
                if not rule_state or not model_state
                else "failed"
                if "failed" in {rule_state, model_state}
                else "skipped"
                if "skipped" in {rule_state, model_state}
                else "complete"
            )
            await self.db.execute(
                text(
                    "UPDATE evaluation_batch_results SET scoring_status=:status,revision=revision+1 WHERE scope_key=:scope AND id=:result AND revision=:expected"
                ),
                params(
                    scope,
                    status=overall,
                    result=candidate.result_id,
                    expected=candidate.result_revision,
                ),
            )
            await self.work.evaluation_batch.event(
                scope,
                candidate.batch_id,
                "scores_appended",
                result_id=candidate.result_id,
                evidence={"evaluation_revision": revision, "source": source},
            )
            await self.work.audit.add(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.score.append",
                    resource_type="evaluation_result",
                    resource_id=str(candidate.result_id),
                    request_id=request_id,
                    metadata={
                        "evaluation_revision": revision,
                        "source": source,
                        "result_revision": candidate.result_revision,
                    },
                )
            )
            await self.db.flush()
            return revision

    async def history(
        self,
        scope,
        batch_id,
        *,
        evaluation_revision,
        result_id=None,
        after_revision=0,
        after_dimension="",
        limit=None,
    ):
        if (
            type(evaluation_revision) is not int
            or evaluation_revision < 0
            or evaluation_revision > await self.revision(scope, batch_id)
        ):
            raise ValueError("evaluation_revision_unavailable")
        rows = (
            await self.db.execute(
                text("""SELECT s.*,v.result_id,v.result_revision,v.run_id,v.run_revision,v.evaluation_revision,v.rubric_revision,v.source,
          public.opencitadel_evaluation_score_judge(v.scope_key,v.id) AS judge_run_id
          FROM evaluation_scores s JOIN evaluation_score_sets v ON v.scope_key=s.scope_key AND v.id=s.set_id
          WHERE v.scope_key=:scope AND v.batch_id=:batch AND v.evaluation_revision<=:revision
          AND (CAST(:result AS uuid) IS NULL OR v.result_id=CAST(:result AS uuid))
          AND (v.evaluation_revision,s.dimension)>(:after_revision,:after_dimension)
          ORDER BY v.evaluation_revision,s.dimension LIMIT :limit"""),
                params(
                    scope,
                    batch=batch_id,
                    revision=evaluation_revision,
                    result=result_id,
                    after_revision=after_revision,
                    after_dimension=after_dimension,
                    limit=limit,
                ),
            )
        ).mappings()
        return tuple(
            ScoreRevision(
                id=r["id"],
                source_set_id=r["set_id"],
                result_id=r["result_id"],
                result_revision=r["result_revision"],
                run_id=r["run_id"],
                judge_run_id=r["judge_run_id"],
                run_revision=r["run_revision"],
                evaluation_revision=r["evaluation_revision"],
                score=ScoreValue(
                    dimension=r["dimension"],
                    source=r["source"],
                    rubric_revision=r["rubric_revision"],
                    value=r["value"],
                    reason=r["reason"],
                    evidence=r["evidence"],
                    status=r["status"],
                    recording=r["recording"],
                ),
                supersedes_id=r["supersedes_id"],
                author=r["created_by"],
                timestamp=r["created_at"],
            )
            for r in rows
        )

    async def heads(self, scope, batch_id, *, evaluation_revision):
        heads = {}
        for row in await self.history(scope, batch_id, evaluation_revision=evaluation_revision):
            heads[
                row.result_id, row.score.rubric_revision, row.score.source, row.score.dimension
            ] = row
        return tuple(heads.values())
