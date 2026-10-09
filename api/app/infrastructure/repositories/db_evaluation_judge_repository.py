"""Private durable judge authority; all mutations borrow the caller transaction."""

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from app.domain.evaluation.batch import ScoringCandidate
from app.domain.evaluation.configuration import digest
from app.domain.models.scope import Principal
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


class DBEvaluationJudgeRepository:
    def __init__(self, work):
        self.work, self.db = work, work.db_session

    async def get(self, scope, run_id):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT i.*,w.status,w.envelope,w.cancel_envelope,w.evaluation_revision,w.error FROM evaluation_judge_intents i JOIN evaluation_judge_work w ON w.scope_key=i.scope_key AND w.intent_id=i.id WHERE i.scope_key=:scope AND i.run_id=:run"
                    ),
                    params(scope, run=run_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def create(
        self,
        scope,
        principal,
        candidate,
        *,
        rubric_id,
        config_id,
        request_id,
        materials,
        namespace_id,
        rescore=None,
        authorizer=None,
    ):
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 255:
            raise ValueError("invalid_judge_request")
        await self.work.evaluation_batch.lock(scope, candidate.batch_id)
        await self.work.evaluation_dataset.authorize(scope, principal, write=True)
        if rescore is not None:
            if authorizer is None:
                raise ValueError("judge_rescore_authorizer_required")
            await self.work.evaluation_dataset.authorize(
                scope.model_copy(update={"user_id": authorizer.user_id}), authorizer, write=True
            )
        identity = uuid5(NAMESPACE_URL, f"judge:{scope}:{candidate.result_id}:{request_id}")
        run = uuid5(identity, "run")
        body = {
            "candidate": candidate.model_dump(mode="json"),
            "rubric": str(rubric_id),
            "config": str(config_id),
            "namespace": str(namespace_id),
            "materials": materials,
            "rescore": rescore,
            "authorizer": authorizer.model_dump(mode="json") if authorizer else None,
        }
        fp = digest(body)
        prior = await self.get(scope, run)
        if prior:
            if prior["fingerprint"] != fp:
                raise ValueError("judge_request_conflict")
            return prior
        if rescore is None and await self.db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM evaluation_judge_intents WHERE scope_key=:scope AND result_id=:result AND rescore IS NULL)"
            ),
            params(scope, result=candidate.result_id),
        ):
            raise ValueError("judge_already_scheduled")
        await self.work.evaluation_score.eligible(scope, principal, candidate, lock=True)
        if rescore is not None and (
            rescore["expected_result_revision"] != candidate.result_revision
            or rescore["expected_evaluation_revision"]
            != await self.work.evaluation_score.revision(scope, candidate.batch_id)
        ):
            raise ValueError("judge_rescore_revision_conflict")
        await self.db.execute(
            text("""INSERT INTO evaluation_judge_intents(id,run_id,batch_id,result_id,namespace_id,rubric_id,config_id,protocol,candidate,materials,request_id,fingerprint,rescore,authorizer,owner_user_id,team_id,created_by)
          VALUES(:id,:run,:batch,:result,:namespace,:rubric,:config,1,CAST(:candidate AS jsonb),CAST(:materials AS jsonb),:request,:fp,CAST(:rescore AS jsonb),CAST(:authorizer AS jsonb),:owner,:team,:actor)"""),
            params(
                scope,
                id=identity,
                run=run,
                batch=candidate.batch_id,
                result=candidate.result_id,
                namespace=namespace_id,
                rubric=rubric_id,
                config=config_id,
                candidate=candidate.model_dump_json(),
                materials=json.dumps(materials),
                request=request_id,
                fp=fp,
                rescore=json.dumps(rescore) if rescore else None,
                authorizer=authorizer.model_dump_json() if authorizer else None,
            ),
        )
        await self.db.execute(
            text("INSERT INTO evaluation_judge_work(scope_key,intent_id) VALUES(:scope,:id)"),
            params(scope, id=identity),
        )
        if rescore is not None:
            from app.domain.models.audit_log import AuditLog

            await self.work.audit.add(
                AuditLog(
                    actor_user_id=authorizer.user_id,
                    team_id=scope.team_id,
                    action="evaluation.rescore.authorize",
                    resource_type="evaluation_result",
                    resource_id=str(candidate.result_id),
                    request_id=request_id,
                    metadata={
                        "judge_intent": str(identity),
                        "source_principal": principal.model_dump(mode="json"),
                        "namespace": str(namespace_id),
                        "rubric_version": str(rubric_id),
                        "judge_config_version": str(config_id),
                        "additional_token_budget": rescore["token_budget"],
                        "additional_money_budget": rescore["money_budget"],
                    },
                )
            )
        return await self.get(scope, run)

    async def current(self, scope, intent, *, lock=False):
        if await self.db.scalar(
            text(
                "SELECT 1 FROM evaluation_review_commands WHERE scope_key=:scope AND kind='cancel' AND judge_run_id=:run LIMIT 1"
            ),
            params(scope, run=intent["run_id"]),
        ):
            raise ValueError("judge_cancelled")
        if intent["rescore"] is not None:
            authorizer = Principal.model_validate(intent["authorizer"])
            await self.work.evaluation_dataset.authorize(
                scope.model_copy(update={"user_id": authorizer.user_id}), authorizer, write=True
            )
        original = ScoringCandidate.model_validate(intent["candidate"])
        # Rule settlement may advance the result revision, and late usage events
        # may advance the terminal run projection. Refresh both through the
        # batch-bound attempt, then revalidate the immutable candidate identity.
        row = (
            (
                await self.db.execute(
                    text(
                        """SELECT r.id,r.revision,a.attempt,a.run_id,a.command_id
                        FROM evaluation_batch_results r JOIN evaluation_batch_attempts a
                          ON a.scope_key=r.scope_key AND a.result_id=r.id AND a.attempt=r.attempt
                        WHERE r.scope_key=:scope AND r.batch_id=:batch AND r.id=:id
                          AND a.run_id=:run"""
                    ),
                    params(
                        scope,
                        batch=original.batch_id,
                        id=original.result_id,
                        run=original.run_id,
                    ),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("scoring_candidate_stale")
        # Keep the refreshed Run version stable until eligible() has validated
        # the same cut. Late usage publication must not turn this internal
        # refresh into a stale-candidate cancellation between the two reads.
        await self.db.execute(
            text(
                "SELECT run_id FROM execution_run_projection WHERE run_id=:run AND source_entity_id=:source AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team FOR SHARE"
            ),
            params(scope, run=row["run_id"], source=str(row["id"]) + ":" + str(row["attempt"])),
        )
        projection = await self.work.evaluation_batch.projection(scope, row)
        if projection is None:
            raise ValueError("scoring_execution_unavailable")
        candidate = original.model_copy(
            update={
                "result_revision": row["revision"],
                "run_revision": projection["stream_version"],
            }
        )
        batch = await self.work.evaluation_batch.get(scope, original.batch_id)
        principal = Principal.model_validate(batch["principal"])
        scope = scope.model_copy(update={"user_id": principal.user_id})
        await self.work.evaluation_score.eligible(scope, principal, candidate, lock=lock)
        for raw in intent["materials"].get("resources", []):
            from app.domain.models.resource_pin import ResourceIdentity

            resource = ResourceIdentity.model_validate(raw)
            await self.work.resource_pins.resolve(scope, resource, lock=lock)
            if resource.resource_kind == "execution_content":
                # Cached text is reusable only while the F06 metadata remains
                # available. A retention pin alone does not prove that.
                from app.domain.models.resource_pin import ResourceUnavailable
                from app.infrastructure.repositories.db_execution_content_repository import (
                    DBExecutionContentRepository,
                )

                binding = (
                    (
                        await self.db.execute(
                            text(
                                "SELECT b.run_id,b.step_id,b.formal_position FROM execution_content_bindings b WHERE b.scope_key=:scope AND b.content_id=CAST(:content AS uuid)"
                            ),
                            params(scope, content=resource.resource_id),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if binding is None:
                    raise ResourceUnavailable("judge_material_unavailable")
                metadata = await DBExecutionContentRepository(self.db).get_snapshot(
                    scope,
                    resource.resource_id,
                    binding["run_id"],
                    binding["step_id"],
                    binding["formal_position"],
                    include_body=False,
                )
                if (
                    metadata is None
                    or metadata["redacted"]
                    or metadata["content_digest"] != resource.resource_version
                ):
                    raise ResourceUnavailable("judge_material_unavailable")
        recording = intent["materials"].get("recording")
        if recording:
            from app.application.evaluation.recording_authority import validate_recording

            manifest = await validate_recording(
                self.work, scope, principal, UUID(recording["version_id"])
            )
            coverage = await self.work.evaluation_recording.coverage(scope, candidate.run_id)
            if manifest.revision != recording["revision"] or any(
                coverage[k] != recording[k] for k in ("total", "consumed", "mismatches")
            ):
                raise ValueError("judge_recording_changed")
        return candidate, principal

    async def active(self, scope, batch_id):
        runs = (
            (
                await self.db.execute(
                    text(
                        "SELECT i.run_id FROM evaluation_judge_intents i JOIN evaluation_judge_work w ON w.scope_key=i.scope_key AND w.intent_id=i.id WHERE i.scope_key=:scope AND i.batch_id=:batch AND w.status IN ('pending','submitted') ORDER BY i.created_at,i.id"
                    ),
                    params(scope, batch=batch_id),
                )
            )
            .scalars()
            .all()
        )
        return [await self.get(scope, run) for run in runs]

    async def update(self, scope, intent, *, status, envelope=None, revision=None, error=None):
        await self.db.execute(
            text(
                "UPDATE evaluation_judge_work SET status=:status,envelope=COALESCE(CAST(:envelope AS jsonb),envelope),evaluation_revision=COALESCE(:revision,evaluation_revision),error=:error WHERE scope_key=:scope AND intent_id=:id"
            ),
            params(
                scope,
                id=intent["id"],
                status=status,
                envelope=envelope.model_dump_json() if envelope else None,
                revision=revision,
                error=error,
            ),
        )

    async def authorize_run(self, scope, run_id, *, state=None, request=None):
        intent = await self.get(scope, run_id)
        binding = await self.work.evaluation_budget_control.binding(scope, run_id)
        if (
            intent is None
            or intent["status"] != "submitted"
            or binding is None
            or binding.purpose != "evaluation_judge"
            or binding.config_version_id != intent["config_id"]
            or binding.namespace_id != intent["namespace_id"]
            or binding.source_entity_id != str(intent["id"])
        ):
            raise ValueError("judge_binding_unavailable")
        if intent["cancel_envelope"] is not None:
            raise ValueError("judge_cancelled")
        await self.current(scope, intent)
        head = (
            (
                await self.db.execute(
                    text(
                        "SELECT execution_revision_id,operations_revision_id FROM runtime_policy_heads WHERE id='global'"
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            head is None
            or str(head["execution_revision_id"]) != binding.policy_revision
            or str(head["operations_revision_id"]) != binding.operations_revision
        ):
            raise ValueError("judge_policy_changed")
        if state is not None and (
            state.source_entity_type != "evaluation_judge"
            or (
                type(state.semantic_payload.get("judge_protocol")) is not int
                or state.semantic_payload["judge_protocol"] != 1
            )
            or state.semantic_payload.get("judge_intent") != str(intent["id"])
        ):
            raise ValueError("judge_protocol_unavailable")
        if await self.unsafe(scope, intent, include_unresolved=False):
            raise ValueError("judge_effect_unknown")
        if request is not None:
            from app.application.execution.decisions.base import activity_identity
            from app.domain.execution.run import decision_data_digest

            ordinal = request.input_payload.get("round")
            if (
                type(ordinal) is not int
                or not 0 <= ordinal <= 2
                or state is None
                or request.activity_id != activity_identity(state, f"model:{ordinal}")
                or request.generation != 0
                or state.retry_generation != 0
            ):
                raise ValueError("judge_round_invalid")
            if await self.unsafe(scope, intent):
                raise ValueError("judge_effect_unresolved")
            for index in range(ordinal):
                prior = activity_identity(state, f"model:{index}")
                expected = {"judge_protocol": 1, "judge_round": index, "judge_status": "invalid"}
                matches = [
                    r
                    for r in state.activity_results
                    if r[0] == prior
                    and r[1] == 0
                    and r[2]
                    and r[4] == decision_data_digest(expected)
                ]
                if len(matches) != 1 or (prior, "succeeded", 0) not in state.settled_activities:
                    raise ValueError("judge_repair_evidence_unavailable")
                payload = await self.db.scalar(
                    text(
                        "SELECT decision_payload FROM execution_activity_tasks WHERE activity_id=:id AND status='succeeded'"
                    ),
                    {"id": prior},
                )
                if payload != expected:
                    raise ValueError("judge_repair_evidence_unavailable")
        return intent

    async def authorize_activity(self, scope, request, context):
        from app.domain.execution.run import RunState

        raw = await self.db.scalar(
            text(
                "SELECT state FROM evaluation_execution_leases WHERE run_id=:run AND phase='held'"
            ),
            {"run": context.run.run_id},
        )
        if raw is None:
            raise ValueError("judge_execution_unavailable")
        return await self.authorize_run(
            scope, context.run.run_id, state=RunState.model_validate(raw), request=request
        )

    @classmethod
    def from_session(cls, session):
        # The command boundary already owns this transaction. No extra checkout.
        from types import SimpleNamespace

        from app.infrastructure.repositories.db_evaluation_batch_repository import (
            DBEvaluationBatchRepository,
        )
        from app.infrastructure.repositories.db_evaluation_budget_control_repository import (
            DBEvaluationBudgetControlRepository,
        )
        from app.infrastructure.repositories.db_evaluation_dataset_repository import (
            DBEvaluationDatasetRepository,
        )
        from app.infrastructure.repositories.db_evaluation_recording_repository import (
            DBEvaluationRecordingRepository,
        )
        from app.infrastructure.repositories.db_evaluation_score_repository import (
            DBEvaluationScoreRepository,
        )
        from app.infrastructure.repositories.db_resource_pin_repository import (
            DBResourcePinRepository,
        )

        work = SimpleNamespace(
            db_session=session,
            evaluation_batch=DBEvaluationBatchRepository(session),
            evaluation_dataset=DBEvaluationDatasetRepository(session),
            evaluation_recording=DBEvaluationRecordingRepository(session),
            evaluation_budget_control=DBEvaluationBudgetControlRepository(session),
            resource_pins=DBResourcePinRepository(session),
        )
        work.evaluation_score = DBEvaluationScoreRepository(work)
        return cls(work)

    async def projection(self, scope, intent):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT state,stream_version,terminal,status FROM execution_run_projection WHERE run_id=:run AND source_entity_type='evaluation_judge' AND source_entity_id=:source AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"
                    ),
                    params(scope, run=intent["run_id"], source=str(intent["id"])),
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def output(self, scope, intent, projection):
        import hashlib

        state = projection["state"]
        result = state.get("result_ref")
        matches = [r for r in state.get("activity_results", []) if result and r[2] == result]
        if len(matches) != 1:
            raise ValueError("judge_result_unavailable")
        activity, generation = matches[0][:2]
        terminal_position = await self.db.scalar(
            text("""SELECT position FROM execution_events
              WHERE stream_type='run' AND stream_id=:run AND event_type='RunCompleted'
              AND stream_version<=:revision
              AND owner_user_id IS NOT DISTINCT FROM :owner
              AND team_id IS NOT DISTINCT FROM :team
              ORDER BY stream_version DESC LIMIT 1"""),
            params(scope, run=str(intent["run_id"]), revision=projection["stream_version"]),
        )
        if terminal_position is None:
            raise ValueError("judge_result_unavailable")
        row = (
            (
                await self.db.execute(
                    text("""SELECT c.body,c.content_digest FROM execution_public_content c JOIN execution_content_bindings b ON b.scope_key=c.scope_key AND b.content_id=c.content_id
          WHERE c.scope_key=:scope AND c.run_id=:run AND b.run_id=:run AND c.activity_id=:activity AND c.generation=:generation
          AND c.phase='output' AND b.phase='output' AND b.formal_position<=:terminal_position AND NOT c.redacted"""),
                    params(
                        scope,
                        run=intent["run_id"],
                        activity=activity,
                        generation=generation,
                        terminal_position=terminal_position,
                    ),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or hashlib.sha256(row["body"].encode()).hexdigest() != row["content_digest"]:
            raise ValueError("judge_result_unavailable")
        value = json.loads(row["body"])
        if (
            value.get("kind") != "model"
            or value.get("message", {}).get("role") != "assistant"
            or value["message"].get("tool_calls")
        ):
            raise ValueError("judge_result_invalid")
        return value["message"]["content"]

    async def cancel(self, scope, batch_id, policy, now, *, run_id=None):
        from app.domain.execution.commands import CommandEnvelope

        for intent in await self.active(scope, batch_id):
            if run_id is not None and intent["run_id"] != run_id:
                continue
            if intent["envelope"] is None:
                await self.stop_unscored(scope, intent, error="judge_cancelled")
                continue
            envelope = CommandEnvelope.model_validate(intent["envelope"])
            receipt = await self.work.evaluation_batch.receipt(
                scope, {"run_id": intent["run_id"], "command_id": envelope.command_id}
            )
            if receipt is None:
                raise ValueError("judge_receipt_unavailable")
            if receipt["status"] != "accepted":
                await self.work.evaluation_execution.withdraw_unaccepted(
                    scope, intent["run_id"], envelope.command_id, policy
                )
                await self.stop_unscored(scope, intent, error="judge_cancelled")
                continue
            projection = await self.projection(scope, intent)
            if projection and projection["terminal"]:
                await self.stop_unscored(scope, intent, error="judge_cancelled")
                continue
            command = envelope.model_copy(
                update={
                    "command_id": uuid5(intent["run_id"], "cancel"),
                    "command_type": "CancelRun",
                    "command_schema_version": 1,
                    "expected_stream_version": None,
                    "issued_at": now,
                    "payload": {"reason": "evaluation_judge_stopped"},
                }
            )
            if intent["cancel_envelope"]:
                command = CommandEnvelope.model_validate(intent["cancel_envelope"])
            else:
                await self.db.execute(
                    text(
                        "UPDATE evaluation_judge_work SET cancel_envelope=CAST(:envelope AS jsonb) WHERE scope_key=:scope AND intent_id=:id"
                    ),
                    params(scope, id=intent["id"], envelope=command.model_dump_json()),
                )
            await self.work.execution_commands.receive(command)
        return len(await self.active(scope, batch_id))

    async def unsafe(self, scope, intent, *, include_unresolved=True):
        # Judge Runs are not subject attempts. Keep their own immutable association
        # and read the same physical unknown/unresolved facts without inventing a
        # subject result row or relying on the last successful provider send.
        return bool(
            await self.db.scalar(
                text("""SELECT EXISTS(SELECT 1 FROM evaluation_judge_invalidations x WHERE x.scope_key=:scope AND x.intent_id=:intent) OR EXISTS(
          SELECT 1 FROM execution_model_dispatches d
          LEFT JOIN evaluation_budget_reservations r ON r.scope_key=d.scope_key AND CAST(r.call_identity AS text)=d.call_identity
          LEFT JOIN execution_model_settlements s ON s.scope_key=d.scope_key AND s.call_identity=d.call_identity
          WHERE d.scope_key=:scope AND d.run_id=:run AND (r.state='unknown' OR (:unresolved AND s.call_identity IS NULL)))
          OR EXISTS(SELECT 1 FROM evaluation_execution_leases l WHERE l.scope_key=:scope AND l.run_id=:run AND
          (l.state->>'failure_code'='NON_IDEMPOTENT_OUTCOME_UNKNOWN'
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(l.state->'settled_activities','[]'::jsonb)) x WHERE x->>1='unknown')
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(l.state->'activity_failure_codes','[]'::jsonb)) x WHERE x->>2='NON_IDEMPOTENT_OUTCOME_UNKNOWN')))"""),
                params(
                    scope, run=intent["run_id"], intent=intent["id"], unresolved=include_unresolved
                ),
            )
        )

    async def observe_unknown(self, scope, batch_id):
        await self.work.evaluation_batch.lock(scope, batch_id)
        runs = (
            (
                await self.db.execute(
                    text(
                        "SELECT i.run_id FROM evaluation_judge_intents i WHERE i.scope_key=:scope AND i.batch_id=:batch AND NOT EXISTS(SELECT 1 FROM evaluation_judge_invalidations x WHERE x.scope_key=i.scope_key AND x.intent_id=i.id)"
                    ),
                    params(scope, batch=batch_id),
                )
            )
            .scalars()
            .all()
        )
        observed = 0
        for run in runs:
            intent = await self.get(scope, run)
            if not await self.unsafe(scope, intent, include_unresolved=False):
                continue
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_score_heads(batch_id,revision,owner_user_id,team_id,created_by) VALUES(:batch,0,:owner,:team,:actor) ON CONFLICT DO NOTHING"
                ),
                params(scope, batch=batch_id),
            )
            revision = await self.db.scalar(
                text(
                    "UPDATE evaluation_score_heads SET revision=revision+1 WHERE scope_key=:scope AND batch_id=:batch RETURNING revision"
                ),
                params(scope, batch=batch_id),
            )
            source = await self.db.scalar(
                text(
                    "SELECT id FROM evaluation_score_sets WHERE scope_key=:scope AND result_id=:result AND source='model' AND request_id=:request"
                ),
                params(scope, result=intent["result_id"], request="judge:" + str(intent["id"])),
            )
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_judge_invalidations(scope_key,intent_id,batch_id,run_id,source_set_id,evaluation_revision) VALUES(:scope,:id,:batch,:run,:source,:revision)"
                ),
                params(
                    scope,
                    id=intent["id"],
                    batch=batch_id,
                    run=run,
                    source=source,
                    revision=revision,
                ),
            )
            await self.work.evaluation_batch.event(
                scope,
                batch_id,
                "judge_effect_unknown",
                result_id=intent["result_id"],
                evidence={"judge_intent": str(intent["id"]), "evaluation_revision": revision},
            )
            observed += 1
        return observed

    async def invalidated_sources(self, scope, batch_id, *, evaluation_revision):
        from app.domain.evaluation.judge_protocol import JudgeSourceInvalidation

        rows = (
            (
                await self.db.execute(
                    text(
                        "SELECT intent_id,run_id,source_set_id,evaluation_revision FROM evaluation_judge_invalidations WHERE scope_key=:scope AND batch_id=:batch AND evaluation_revision<=:revision ORDER BY evaluation_revision"
                    ),
                    params(scope, batch=batch_id, revision=evaluation_revision),
                )
            )
            .mappings()
            .all()
        )
        return tuple(JudgeSourceInvalidation.model_validate(dict(row)) for row in rows)

    async def ready_batches(self, *, limit):
        from app.domain.models.scope import OwnerScope

        rows = (
            (
                await self.db.execute(
                    text(
                        "SELECT b.scope_body,b.id,min(w.checked_at) AS checked FROM evaluation_judge_intents i JOIN evaluation_judge_work w ON w.scope_key=i.scope_key AND w.intent_id=i.id JOIN evaluation_batches b ON b.scope_key=i.scope_key AND b.id=i.batch_id GROUP BY b.scope_body,b.id ORDER BY checked,b.id LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
        return tuple((OwnerScope.model_validate(r["scope_body"]), r["id"]) for r in rows)

    async def checked(self, scope, batch_id):
        await self.db.execute(
            text(
                "UPDATE evaluation_judge_work w SET checked_at=clock_timestamp() FROM evaluation_judge_intents i WHERE i.scope_key=w.scope_key AND i.id=w.intent_id AND i.scope_key=:scope AND i.batch_id=:batch"
            ),
            params(scope, batch=batch_id),
        )

    async def stop_unscored(self, scope, intent, *, error):
        await self.update(scope, intent, status="stopped", error=error)
        if not intent["rescore"]:
            rows = await self.work.evaluation_batch.results(scope, intent["batch_id"], limit=5000)
            row = next(r for r in rows if r["id"] == intent["result_id"])
            if row["scoring_status"] == "pending":
                await self.work.evaluation_batch.update_result(
                    scope,
                    intent["batch_id"],
                    row,
                    row["execution_status"],
                    scoring="skipped",
                    error=error,
                )
        elif await self.work.evaluation_budget_control.binding(scope, intent["run_id"]):
            namespace = await self.work.evaluation_budget_control.namespace(
                scope, intent["namespace_id"], lock=True
            )
            if namespace.state == "open":
                await self.work.evaluation_budget_control.close(
                    scope, intent["namespace_id"], expected_revision=namespace.revision
                )
