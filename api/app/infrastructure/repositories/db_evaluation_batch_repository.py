"""Scoped durable orchestration. All mutations borrow the caller transaction.

Lock order is batch before namespace; execution/usage consumers never lock batches.
HTTP command insertion uses the same batch advisory lock as dispatch fencing.
"""

import hashlib
import json
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import text

from app.application.execution.admission import run_id_for_idempotency_key
from app.domain.evaluation.batch import admission_key
from app.domain.evaluation.configuration import digest
from app.domain.evaluation.errors import DatasetNotFound
from app.infrastructure.execution.original_evidence import retain_read
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


def effect_query(scope=":scope", run=":run", unresolved="false"):
    # Only code-owned SQL expressions are passed here, never request strings.
    return f"SELECT public.opencitadel_e06_effect_unsafe({scope},{run},{unresolved},NULL,NULL)"


class DBEvaluationBatchRepository:
    def __init__(self, db_session, *, signing_secret=None):
        self.db, self.signing_secret = db_session, signing_secret
        self.new_command = None

    async def lock(self, scope, batch_id):
        key = int.from_bytes(
            hashlib.sha256(
                f"evaluation-batch:{params(scope)['scope']}:{batch_id}".encode()
            ).digest()[:8],
            "big",
            signed=True,
        )
        await self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    async def submit(self, scope, principal, kind, request_id, payload, batch_id):
        if (
            kind not in {"start", "cancel", "retry_failed"}
            or not request_id.strip()
            or len(request_id) > 255
        ):
            raise ValueError("invalid_batch_command")
        request_key = int.from_bytes(
            hashlib.sha256(
                f"evaluation-request:{params(scope)['scope']}:{kind}:{request_id}".encode()
            ).digest()[:8],
            "big",
            signed=True,
        )
        await self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": request_key})
        fp = digest(payload["request_payload"] if kind == "retry_failed" else payload)
        prior = (
            (
                await self.db.execute(
                    text(
                        "SELECT batch_id,fingerprint FROM evaluation_batch_commands WHERE scope_key=:scope AND kind=:kind AND request_id=:request"
                    ),
                    params(scope, kind=kind, request=request_id),
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["fingerprint"] != fp:
                raise ValueError("request_conflict")
            return prior["batch_id"]
        await self.lock(scope, batch_id)
        if kind in {"start", "retry_failed"}:
            await self.db.execute(
                text(
                    "INSERT INTO evaluation_batches(id,suite_version,principal,scope_body,parent_batch,selected_slots,owner_user_id,team_id,created_by) VALUES(:id,:suite,CAST(:principal AS jsonb),CAST(:scope_body AS jsonb),:parent,CAST(:slots AS jsonb),:owner,:team,:actor)"
                ),
                params(
                    scope,
                    id=batch_id,
                    suite=UUID(payload["suite_version"]),
                    principal=json.dumps(principal.model_dump(mode="json")),
                    scope_body=json.dumps(scope.model_dump(mode="json")),
                    parent=UUID(payload["parent_batch"]) if payload.get("parent_batch") else None,
                    slots=json.dumps(payload.get("selected_slots")),
                ),
            )
        else:
            await self.get(scope, batch_id)
        command_id = uuid4()
        await self.db.execute(
            text(
                "INSERT INTO evaluation_batch_commands(id,batch_id,request_id,kind,fingerprint,payload,owner_user_id,team_id,created_by) VALUES(:id,:batch,:request,:kind,:fp,CAST(:payload AS jsonb),:owner,:team,:actor)"
            ),
            params(
                scope,
                id=command_id,
                batch=batch_id,
                request=request_id,
                kind=kind,
                fp=fp,
                payload=json.dumps(payload),
            ),
        )
        self.new_command = command_id
        return batch_id

    async def command(self, scope, kind, request_id, payload):
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT batch_id,fingerprint FROM evaluation_batch_commands WHERE scope_key=:scope AND kind=:kind AND request_id=:request"
                    ),
                    params(scope, kind=kind, request=request_id),
                )
            )
            .mappings()
            .first()
        )
        if row is not None and row["fingerprint"] != digest(payload):
            raise ValueError("request_conflict")
        return row["batch_id"] if row else None

    async def get(self, scope, batch_id):
        result = await self.db.execute(
            text("SELECT * FROM evaluation_batches WHERE scope_key=:scope AND id=:id"),
            params(scope, id=batch_id),
        )
        try:
            row = result.mappings().first()
            retain_read(
                self.db,
                "batch-source",
                "batch.get",
                {"scope": scope, "id": batch_id},
                row,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)
        if row is None:
            raise DatasetNotFound("batch_unavailable")
        return dict(row)

    async def claim(self, now, *, lease_seconds=30):
        row = (
            (
                await self.db.execute(
                    text(f"""WITH candidate AS (
          SELECT scope_key,id FROM evaluation_batches source WHERE (claim_until IS NULL OR claim_until<=:now)
          AND (status NOT IN ('rejected','completed','completed_with_errors','failed','cancelled') OR cleanup_status!='clean' OR EXISTS(SELECT 1 FROM evaluation_batch_results result JOIN evaluation_batch_attempts attempt ON attempt.scope_key=result.scope_key AND attempt.result_id=result.id AND attempt.attempt=result.attempt WHERE result.scope_key=source.scope_key AND result.batch_id=source.id AND NOT result.unknown_effect AND ({effect_query("source.scope_key", "attempt.run_id")})))
          ORDER BY last_tick,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
          UPDATE evaluation_batches b SET generation=generation+1,claim_until=:until,last_tick=:now FROM candidate c
          WHERE b.scope_key=c.scope_key AND b.id=c.id RETURNING b.*"""),
                    {"now": now, "until": now + timedelta(seconds=lease_seconds)},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def fence(self, claim, *, dispatch=False, result_id=None):
        from app.domain.models.scope import OwnerScope

        scope = OwnerScope.model_validate(claim["scope_body"])
        await self.lock(scope, claim["id"])
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT *,clock_timestamp() AS database_now FROM evaluation_batches WHERE scope_key=:scope AND id=:id AND generation=:generation AND claim_until>clock_timestamp() FOR UPDATE"
                    ),
                    params(scope, id=claim["id"], generation=claim["generation"]),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("claim_lost")
        if dispatch:
            cancelled = await self.db.scalar(
                text(
                    "SELECT 1 FROM evaluation_batch_commands WHERE scope_key=:scope AND batch_id=:id AND kind='cancel' LIMIT 1"
                ),
                params(scope, id=claim["id"]),
            )
            if (
                cancelled
                or row["cancel_requested"]
                or row["status"] not in {"queued", "running", "waiting"}
                or (row["deadline"] is not None and row["deadline"] <= row["database_now"])
            ):
                raise ValueError("batch_dispatch_stopped")
            if result_id is not None:
                expired = await self.db.scalar(
                    text(
                        "SELECT started_at+make_interval(secs=>:timeout)<=clock_timestamp() FROM evaluation_batch_results WHERE scope_key=:scope AND id=:result"
                    ),
                    params(
                        scope, result=result_id, timeout=row["settings"]["case_timeout_seconds"]
                    ),
                )
                if expired:
                    raise ValueError("case_dispatch_stopped")
        return dict(row)

    async def materialize(self, claim, slots, settings, *, review_required=False):
        from app.domain.models.scope import OwnerScope

        scope = OwnerScope.model_validate(claim["scope_body"])
        await self.fence(claim)
        if not 1 <= len(slots) <= 5000:
            raise ValueError("invalid_matrix")
        rows, attempts = [], []
        for ordinal, slot in enumerate(slots):
            key = admission_key(
                str(claim["id"]),
                str(slot.case_revision_id),
                str(slot.config_version_id),
                slot.repetition,
                0,
            )
            result = uuid5(NAMESPACE_URL, key + ":result")
            rows.append(
                params(
                    scope,
                    id=result,
                    batch=claim["id"],
                    case=slot.case_revision_id,
                    config=slot.config_version_id,
                    repeat=slot.repetition,
                    ordinal=ordinal,
                )
            )
            attempts.append(
                params(
                    scope,
                    result=result,
                    run=run_id_for_idempotency_key(key),
                    command=uuid5(NAMESPACE_URL, f"opencitadel:admit:{key}"),
                    key=key,
                )
            )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_batch_results(id,batch_id,case_revision_id,config_version_id,repetition,ordinal,owner_user_id,team_id,created_by) VALUES(:id,:batch,:case,:config,:repeat,:ordinal,:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            rows,
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_batch_attempts(result_id,attempt,run_id,command_id,admission_key,owner_user_id,team_id,created_by) VALUES(:result,0,:run,:command,:key,:owner,:team,:actor) ON CONFLICT DO NOTHING"
            ),
            attempts,
        )
        await self.db.execute(
            text(
                "UPDATE evaluation_batches SET settings=CAST(:settings AS jsonb),review_status=CASE WHEN :review THEN 'pending' ELSE review_status END,status='queued',started_at=COALESCE(started_at,clock_timestamp()),deadline=COALESCE(deadline,clock_timestamp()+make_interval(secs=>:timeout)) WHERE scope_key=:scope AND id=:id"
            ),
            params(
                scope,
                id=claim["id"],
                settings=json.dumps(settings),
                timeout=settings["batch_timeout_seconds"],
                review=review_required,
            ),
        )

    async def results(self, scope, batch_id, *, after=-1, limit=100):
        from app.infrastructure.execution.query_observation import named_query

        result = await self.db.execute(
            named_query(
                text(
                    "SELECT r.*,a.run_id,a.command_id,a.admission_key,a.intent,a.prepared_envelope,a.envelope,a.receipt,a.status AS admission_status,a.run_revision,a.dispatched_at,a.cancel_sent,a.cancel_command_id,a.predecessor_run_id,a.predecessor_generation,a.available_at FROM evaluation_batch_results r JOIN evaluation_batch_attempts a ON a.scope_key=r.scope_key AND a.result_id=r.id AND a.attempt=r.attempt WHERE r.scope_key=:scope AND r.batch_id=:id AND r.ordinal>:after ORDER BY r.ordinal LIMIT :limit"
                ),
                "matrix.results",
            ),
            params(scope, id=batch_id, after=after, limit=limit),
        )
        try:
            output = []
            for row in result.mappings():
                retain_read(
                    self.db,
                    "batch-source",
                    "batch.results",
                    {"scope": scope, "id": batch_id, "after": after, "limit": limit},
                    row,
                    source_result=result,
                )
                output.append(dict(row))
            if not output:
                retain_read(
                    self.db,
                    "batch-source",
                    "batch.results",
                    {"scope": scope, "id": batch_id, "after": after, "limit": limit},
                    [],
                    source_result=result,
                )
            return output
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)

    async def dispatch_candidates(self, claim, now, *, limit):
        from app.domain.models.scope import OwnerScope

        scope = OwnerScope.model_validate(claim["scope_body"])
        batch = await self.fence(claim, dispatch=True)
        rows = await self.results(scope, claim["id"], limit=5000)
        eligible = [
            row
            for row in rows
            if row["execution_status"] in {"queued", "waiting"}
            and row["envelope"] is None
            and row["available_at"] <= now
        ]
        eligible.sort(key=lambda row: (row["ordinal"] <= batch["dispatch_cursor"], row["ordinal"]))
        selected = eligible[:limit]
        if selected:
            await self.db.execute(
                text(
                    "UPDATE evaluation_batch_results SET started_at=COALESCE(started_at,:now) WHERE scope_key=:scope AND id=:id"
                ),
                [params(scope, id=row["id"], now=now) for row in selected],
            )
            await self.db.execute(
                text(
                    "UPDATE evaluation_batches SET dispatch_cursor=:cursor WHERE scope_key=:scope AND id=:id"
                ),
                params(scope, id=claim["id"], cursor=selected[-1]["ordinal"]),
            )
        return selected

    async def renew(self, claim, *, lease_seconds=30):
        """Renew only an unexpired exact generation; never revive a stale owner."""
        renewed = await self.db.scalar(
            text(
                "UPDATE evaluation_batches SET claim_until=clock_timestamp()+make_interval(secs=>:seconds) WHERE scope_key=:scope AND id=:id AND generation=:generation AND claim_until>clock_timestamp() RETURNING generation"
            ),
            {
                "scope": claim["scope_key"],
                "id": claim["id"],
                "generation": claim["generation"],
                "seconds": lease_seconds,
            },
        )
        if renewed is None:
            raise ValueError("claim_lost")

    async def release(self, claim):
        await self.db.execute(
            text(
                "UPDATE evaluation_batches SET claim_until=NULL WHERE scope_key=:scope AND id=:id AND generation=:generation"
            ),
            {"scope": claim["scope_key"], "id": claim["id"], "generation": claim["generation"]},
        )

    async def event(self, scope, batch_id, kind, *, result_id=None, evidence=None):
        revision = await self.db.scalar(
            text(
                "UPDATE evaluation_batches SET revision=revision+1 WHERE scope_key=:scope AND id=:id RETURNING revision"
            ),
            params(scope, id=batch_id),
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_batch_events(id,batch_id,revision,kind,result_id,evidence,owner_user_id,team_id,created_by) VALUES(:id,:batch,:revision,:kind,:result,CAST(:evidence AS jsonb),:owner,:team,:actor)"
            ),
            params(
                scope,
                id=uuid4(),
                batch=batch_id,
                revision=revision,
                kind=kind,
                result=result_id,
                evidence=json.dumps(evidence or {}),
            ),
        )

    async def set_status(self, scope, batch_id, status, *, error=None):
        await self.db.execute(
            text(
                "UPDATE evaluation_batches SET status=:status,error=COALESCE(:error,error) WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=batch_id, status=status, error=error),
        )
        await self.event(scope, batch_id, "state_changed")

    async def update_result(
        self, scope, batch_id, result, execution, *, scoring=None, error=None, unknown=False
    ):
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_results SET execution_status=:execution,scoring_status=COALESCE(:scoring,scoring_status),error=COALESCE(:error,error),unknown_effect=unknown_effect OR :unknown,revision=revision+1 WHERE scope_key=:scope AND id=:id"
            ),
            params(
                scope,
                id=result["id"],
                execution=execution,
                scoring=scoring,
                error=error,
                unknown=unknown,
            ),
        )
        await self.event(scope, batch_id, "result_changed", result_id=result["id"])

    async def save_intent(self, scope, row, intent):
        previous = await self.db.scalar(
            text(
                "SELECT intent FROM evaluation_batch_attempts WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt"
            ),
            params(scope, id=row["id"], attempt=row["attempt"]),
        )
        if previous is not None and previous != intent:
            raise ValueError("admission_intent_changed")
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_attempts SET intent=CAST(:intent AS jsonb) WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt AND intent IS NULL"
            ),
            params(scope, id=row["id"], attempt=row["attempt"], intent=json.dumps(intent)),
        )

    async def prepared(self, scope, row, envelope):
        saved = await self.db.scalar(
            text(
                "SELECT prepared_envelope FROM evaluation_batch_attempts WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt"
            ),
            params(scope, id=row["id"], attempt=row["attempt"]),
        )
        if saved is not None and saved != envelope.model_dump(mode="json"):
            raise ValueError("admission_envelope_changed")
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_attempts SET prepared_envelope=CAST(:envelope AS jsonb) WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt AND prepared_envelope IS NULL"
            ),
            params(
                scope, id=row["id"], attempt=row["attempt"], envelope=envelope.model_dump_json()
            ),
        )

    async def submitted(self, scope, batch_id, row, envelope):
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_attempts SET envelope=CAST(:envelope AS jsonb),status='submitted',dispatched_at=COALESCE(dispatched_at,clock_timestamp()) WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt"
            ),
            params(
                scope, id=row["id"], attempt=row["attempt"], envelope=envelope.model_dump_json()
            ),
        )
        await self.update_result(scope, batch_id, row, "admitting")

    async def receipt(self, scope, row):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT status,rejection_code,last_error_code,first_event_position,last_event_position FROM execution_command_inbox WHERE command_id=:command AND stream_type='run' AND stream_id=:run AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"
                    ),
                    params(scope, command=row["command_id"], run=str(row["run_id"])),
                )
            )
            .mappings()
            .first()
        )

    async def projection(self, scope, row):
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT status,state,stream_version,terminal FROM execution_run_projection WHERE run_id=:run AND source_entity_id=:source AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"
                    ),
                    params(
                        scope, run=row["run_id"], source=str(row["id"]) + ":" + str(row["attempt"])
                    ),
                )
            )
            .mappings()
            .first()
        )

    async def record_receipt(self, scope, row, receipt, version=0):
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_attempts SET receipt=CAST(:receipt AS jsonb),status=:status,run_revision=GREATEST(run_revision,:version) WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt"
            ),
            params(
                scope,
                id=row["id"],
                attempt=row["attempt"],
                receipt=json.dumps(dict(receipt)),
                status=receipt["status"],
                version=version,
            ),
        )

    async def attach_received(self, scope, batch_id, row):
        from app.domain.execution.commands import CommandEnvelope

        saved = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM execution_command_inbox WHERE command_id=:command AND stream_id=:run AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"
                    ),
                    params(scope, command=row["command_id"], run=str(row["run_id"])),
                )
            )
            .mappings()
            .one()
        )
        if saved["command_type"] != "CreateRun" or saved["payload_digest"] is not None:
            return
        envelope = CommandEnvelope.model_validate(
            {name: saved[name] for name in CommandEnvelope.model_fields if name in saved}
        )
        if envelope.payload["source_entity_id"] != str(row["id"]) + ":" + str(row["attempt"]) or (
            row["intent"] and envelope.payload["policy_snapshot"] != row["intent"]["policy"]
        ):
            raise ValueError("admission_receipt_mismatch")
        await self.submitted(scope, batch_id, row, envelope)

    async def counts(self, scope, batch_id):
        result = await self.db.execute(
            text(
                "SELECT execution_status,count(*) FROM evaluation_batch_results WHERE scope_key=:scope AND batch_id=:id GROUP BY execution_status"
            ),
            params(scope, id=batch_id),
        )
        try:
            rows = result.all()
            retain_read(
                self.db,
                "batch-source",
                "batch.counts",
                {"scope": scope, "id": batch_id},
                rows,
                source_result=result,
            )
            return dict(rows)
        finally:
            result.close()
            synchronous = getattr(self.db, "sync_session", self.db)
            forget_result = getattr(synchronous, "forget_result", None)
            if callable(forget_result):
                forget_result(result)

    async def preflight(self, scope, suite_id, revision):
        return await self.db.scalar(
            text(
                "SELECT body FROM evaluation_preflights WHERE scope_key=:scope AND suite_version=:suite AND revision=:revision"
            ),
            params(scope, suite=suite_id, revision=revision),
        )

    async def cancellation_requested(self, scope, batch_id):
        return bool(
            await self.db.scalar(
                text(
                    "SELECT 1 FROM evaluation_batch_commands WHERE scope_key=:scope AND batch_id=:id AND kind='cancel' LIMIT 1"
                ),
                params(scope, id=batch_id),
            )
        )

    async def set_cleanup(self, scope, batch_id, status):
        await self.db.execute(
            text(
                "UPDATE evaluation_batches SET cleanup_status=:status WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=batch_id, status=status),
        )

    async def environment_statuses(self, scope, batch_id, *, after=None, limit=101):
        # Bound leases before aggregating retained operations; no private lease or
        # operation payload is selected. A failed prior revision is durable evidence
        # of quarantine in this generation, never a fabricated failure timestamp.
        return list(
            (
                await self.db.execute(
                    text("""
            WITH page AS (
                SELECT id, environment_version, case_slot, generation, revision, state
                FROM evaluation_environment_leases
                WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch
                  AND (CAST(:after AS uuid) IS NULL OR id>CAST(:after AS uuid))
                ORDER BY id LIMIT :limit
            ), failures AS (
                SELECT operation.lease_id, operation.phase, count(*) AS count
                FROM evaluation_environment_operations operation JOIN page
                  ON page.id=operation.lease_id AND page.generation=operation.generation
                WHERE operation.scope_key=:scope AND operation.status='failed'
                  AND operation.lease_revision<page.revision
                GROUP BY operation.lease_id, operation.phase
            ), history AS (
                SELECT lease_id, jsonb_object_agg(phase,count) AS evidence
                FROM failures GROUP BY lease_id
            )
            SELECT page.id, page.environment_version, page.generation, page.revision, page.state,
                   (page.case_slot->>'case_id')::uuid AS case_id,
                   (page.case_slot->>'config_version')::uuid AS config_version,
                   (page.case_slot->>'repeat')::int AS repeat,
                   COALESCE(history.evidence,'{}'::jsonb) AS prior_failed_operations
            FROM page LEFT JOIN history ON history.lease_id=page.id ORDER BY page.id
        """),
                    params(scope, batch=str(batch_id), after=after, limit=limit),
                )
            ).mappings()
        )

    async def environment_leases(self, scope, batch_id):
        return list(
            (
                await self.db.execute(
                    text(
                        "SELECT id,state,case_slot FROM evaluation_environment_leases WHERE scope_key=:scope AND case_slot->>'batch_id'=:batch"
                    ),
                    params(scope, batch=str(batch_id)),
                )
            ).mappings()
        )

    async def unknown_effect(self, scope, run_id, *, include_unresolved=False, principal=None):
        encoded = signature = None
        if principal is not None:
            from app.domain.models.authorization import AuthorizationContext
            from app.infrastructure.repositories.db_physical_requester_repository import (
                DBPhysicalRequesterRepository,
            )

            sealed = await DBPhysicalRequesterRepository(
                self.db, signing_secret=self.signing_secret
            ).capture(
                scope,
                AuthorizationContext.for_principal(principal, scope=scope),
                run_id=run_id,
            )
            encoded = json.dumps(
                sealed["proof"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            signature = sealed["signature"]
        return bool(
            await self.db.scalar(
                text(
                    "SELECT public.opencitadel_e06_effect_unsafe(:scope,:run,:unresolved,:encoded,:signature)"
                ),
                params(
                    scope,
                    run=run_id,
                    unresolved=include_unresolved,
                    encoded=encoded,
                    signature=signature,
                ),
            )
        )

    async def observe_unknown(self, scope, batch_id, row):
        """Monotonic evidence only; never change a terminal execution outcome."""
        if row["unknown_effect"] or not await self.unknown_effect(scope, row["run_id"]):
            return
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_results SET unknown_effect=true,revision=revision+1 WHERE scope_key=:scope AND id=:id AND NOT unknown_effect"
            ),
            params(scope, id=row["id"]),
        )
        row["unknown_effect"] = True
        await self.event(scope, batch_id, "unknown_effect_observed", result_id=row["id"])

    async def cancel_submitted(self, scope, row, command_id):
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_attempts SET cancel_sent=true,cancel_command_id=:command WHERE scope_key=:scope AND result_id=:id AND attempt=:attempt"
            ),
            params(scope, command=command_id, id=row["id"], attempt=row["attempt"]),
        )

    async def retry_predecessor(self, scope, row):
        """Eligibility is derived from accepted facts, not a caller retryability flag."""
        if row["attempt"] >= 2 or row["unknown_effect"] or row["execution_status"] != "failed":
            return None
        lease = (
            (
                await self.db.execute(
                    text(
                        "SELECT generation,phase,state FROM evaluation_execution_leases WHERE scope_key=:scope AND run_id=:run"
                    ),
                    params(scope, run=row["run_id"]),
                )
            )
            .mappings()
            .first()
        )
        if (
            not lease
            or lease["phase"] != "released"
            or not lease["state"]
            or lease["state"]["status"] != "failed"
            or lease["state"].get("failure_code") != "ACTIVITY_TIMEOUT"
        ):
            return None
        state = lease["state"]
        failures = [
            item
            for item in state.get("activity_failure_codes", [])
            if item[1] == state["retry_generation"]
        ]
        if not failures or any(item[2] != "ACTIVITY_TIMEOUT" for item in failures):
            return None
        for activity, generation, _ in failures:
            task = (
                (
                    await self.db.execute(
                        text(
                            "SELECT call_started_at,attempt FROM execution_activity_tasks WHERE aggregate_type='run' AND aggregate_id=:run AND activity_id=:activity AND request_generation=:generation"
                        ),
                        {
                            "run": str(row["run_id"]),
                            "activity": UUID(activity),
                            "generation": generation,
                        },
                    )
                )
                .mappings()
                .first()
            )
            started = await self.db.scalar(
                text(
                    "SELECT 1 FROM execution_events WHERE stream_type='run' AND stream_id=:run AND event_type='ActivityCallStarted' AND public_payload->>'activity_id'=:activity AND CAST(public_payload->>'generation' AS integer)=:generation LIMIT 1"
                ),
                {"run": str(row["run_id"]), "activity": activity, "generation": generation},
            )
            if not task or task["call_started_at"] is not None or task["attempt"] != 0 or started:
                return None
        unresolved = await self.unknown_effect(scope, row["run_id"], include_unresolved=True)
        states = (
            (
                await self.db.execute(
                    text(
                        "SELECT l.state FROM evaluation_environment_bindings b JOIN evaluation_environment_leases l ON l.scope_key=b.scope_key AND l.id=b.lease_id WHERE b.scope_key=:scope AND b.run_id=:run"
                    ),
                    params(scope, run=row["run_id"]),
                )
            )
            .scalars()
            .all()
        )
        if unresolved or any(item[1] == "unknown" for item in state.get("settled_activities", [])):
            return None
        from app.domain.evaluation.batch import RecoveryPredecessor

        return RecoveryPredecessor(
            generation=lease["generation"],
            cleanup="failed"
            if "quarantine" in states
            else "pending"
            if any(value != "verified_clean" for value in states)
            else "ready",
        )

    async def set_recovery(self, scope, batch_id, row, pending, *, reason=None):
        if row["recovery_pending"] == pending and reason is None:
            return
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_results SET recovery_pending=:pending,error=COALESCE(:reason,error),revision=revision+1 WHERE scope_key=:scope AND id=:id"
            ),
            params(scope, id=row["id"], pending=pending, reason=reason),
        )
        await self.event(
            scope,
            batch_id,
            "recovery_pending" if pending else "recovery_settled",
            result_id=row["id"],
            evidence={"reason": reason},
        )

    async def replace_attempt(self, scope, batch_id, row, predecessor_generation, now):
        attempt = row["attempt"] + 1
        if attempt > 2:
            raise ValueError("case_attempt_limit")
        key = admission_key(
            str(batch_id),
            str(row["case_revision_id"]),
            str(row["config_version_id"]),
            row["repetition"],
            attempt,
        )
        await self.db.execute(
            text(
                "INSERT INTO evaluation_batch_attempts(result_id,attempt,run_id,command_id,admission_key,predecessor_run_id,predecessor_generation,available_at,owner_user_id,team_id,created_by) VALUES(:result,:attempt,:run,:command,:key,:predecessor,:generation,:available,:owner,:team,:actor)"
            ),
            params(
                scope,
                result=row["id"],
                attempt=attempt,
                run=run_id_for_idempotency_key(key),
                command=uuid5(NAMESPACE_URL, f"opencitadel:admit:{key}"),
                key=key,
                predecessor=row["run_id"],
                generation=predecessor_generation,
                available=now + timedelta(seconds=2**attempt),
            ),
        )
        await self.db.execute(
            text(
                "UPDATE evaluation_batch_results SET attempt=:attempt,recovery_pending=false,execution_status='queued',scoring_status='pending',error=NULL,revision=revision+1 WHERE scope_key=:scope AND id=:id AND attempt=:prior"
            ),
            params(scope, id=row["id"], attempt=attempt, prior=row["attempt"]),
        )
        await self.event(scope, batch_id, "infrastructure_retry", result_id=row["id"])

    async def budget_availability(self, batch_id, config):
        from decimal import Decimal

        bucket = (
            (
                await self.db.execute(
                    text("SELECT * FROM evaluation_budget_buckets WHERE key=:key"),
                    {"key": "5:batch:" + str(batch_id)},
                )
            )
            .mappings()
            .first()
        )
        if bucket is None:
            return "ready"
        candidates = config.snapshot["budget"]["candidates"]
        tokens = max(candidate["tokens"] for candidate in candidates)
        if bucket["breached"] or bucket["spent_tokens"] + tokens > bucket["limits"]["tokens"]:
            return "blocked_budget"
        reserved = (
            bucket["spent_tokens"] + bucket["reserved_tokens"] + tokens > bucket["limits"]["tokens"]
        )
        if "money" in bucket["limits"]:
            if any(candidate["money"] is None for candidate in candidates):
                return "blocked_budget"
            money = max(Decimal(candidate["money"]) for candidate in candidates)
            limit = Decimal(str(bucket["limits"]["money"]))
            if bucket["spent_money"] + money > limit:
                return "blocked_budget"
            reserved = reserved or bucket["spent_money"] + bucket["reserved_money"] + money > limit
        return "waiting" if reserved else "ready"

    async def scoring_candidates(self, scope, batch_id, *, limit=100):
        from app.domain.evaluation.batch import ScoringCandidate

        batch = await self.get(scope, batch_id)
        if batch["status"] not in {
            "running",
            "waiting",
            "queued",
        } or await self.cancellation_requested(scope, batch_id):
            return []
        candidates = []
        for row in await self.results(scope, batch_id, limit=5000):
            if (
                row["execution_status"] != "succeeded"
                or row["scoring_status"] != "pending"
                or row["unknown_effect"]
                or await self.unknown_effect(scope, row["run_id"], include_unresolved=True)
            ):
                continue
            receipt = await self.receipt(scope, row)
            projection = await self.projection(scope, row)
            if (
                receipt is None
                or receipt["status"] != "accepted"
                or projection is None
                or projection["status"] != "completed"
                or not projection["terminal"]
            ):
                continue
            candidates.append(
                ScoringCandidate(
                    batch_id=batch_id,
                    result_id=row["id"],
                    result_revision=row["revision"],
                    run_id=row["run_id"],
                    run_revision=projection["stream_version"],
                    suite_version_id=batch["suite_version"],
                    case_revision_id=row["case_revision_id"],
                    config_version_id=row["config_version_id"],
                )
            )
            if len(candidates) >= limit:
                break
        return candidates
