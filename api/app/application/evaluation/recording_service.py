"""Asynchronous recording creation; source reads are exclusively authorized F06 pages."""

import logging
from uuid import UUID, uuid4

from app.application.evaluation.dataset_service import fingerprint
from app.domain.evaluation.recording import RecordingJob, RecordingSelection
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext

logger = logging.getLogger(__name__)


def public_job(row):
    return RecordingJob(**{key: row[key] for key in RecordingJob.model_fields if key in row})


class RecordingService:
    def __init__(self, uow_factory, *, source, cursor_secret=None):
        self.cursor_secret = cursor_secret
        self.uow_factory, self.source = uow_factory, source

    def auth(self, scope, principal, request_id=""):
        return AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)

    async def list_jobs(self, scope, principal, *, cursor=None, limit=50):
        from app.application.evaluation.discovery import RecordingPage
        from app.application.evaluation.discovery_cursor import decode, encode

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = ["recordings", scope.model_dump(mode="json"), principal.user_id]
        after = UUID(decode(self.cursor_secret, context, cursor)) if cursor else None
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            rows = await uow.evaluation_recording.list_jobs(scope, after=after, limit=limit + 1)
        items = []
        for row in rows[:limit]:
            await self.source.views.get_view(scope, row["source_run_id"])
            if row["status"] == "ready":
                await self.result(scope, principal, row["id"])
            items.append(public_job(row))
        return RecordingPage(
            items=tuple(items),
            next_cursor=encode(self.cursor_secret, context, str(rows[limit - 1]["id"]))
            if len(rows) > limit
            else None,
        )

    async def candidates(self, scope, principal, run_id, *, cursor=None, limit=50):
        from app.application.evaluation.discovery import (
            RecordingCandidate,
            RecordingCandidatePage,
            RecordingField,
        )
        from app.application.evaluation.discovery_cursor import decode, encode
        from app.application.evaluation.recording_worker import retrieval_contract
        from app.domain.evaluation.errors import ReplayMismatch
        from app.domain.evaluation.recording import (
            MAX_RECORDING_TOTAL_BYTES,
            RecordedContract,
            canonical,
        )
        from app.domain.models.tool_result import ToolResult

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = [
            "recording_candidates",
            scope.model_dump(mode="json"),
            principal.user_id,
            str(run_id),
        ]
        position = decode(self.cursor_secret, context, cursor) if cursor else None
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
        at, steps = await self.source.steps(scope, run_id, at=position["at"] if position else None)
        if any(step.activity_id is not None and step.status != "completed" for step in steps):
            raise ReplayMismatch("source_activity_incomplete")
        evidence, contracts, size = [], {}, 0
        for step in steps:
            if step.activity_id is None or step.status != "completed":
                continue
            inp, redacted, _ = await self.source.complete(scope, run_id, step.step_id, at, "input")
            out, _, _ = await self.source.complete(scope, run_id, step.step_id, at, "output")
            size += len(canonical(inp)) + len(canonical(out))
            if size > MAX_RECORDING_TOTAL_BYTES:
                raise ReplayMismatch("recording_total_size_limit")
            if out.get("kind") == "model":
                async with self.uow_factory(self.auth(scope, principal)) as uow:
                    await uow.evaluation_dataset.authorize(scope, principal, write=True)
                    captured = await uow.evaluation_recording.captured(
                        scope, run_id, step.activity_id
                    )
                for raw in captured["contracts"]:
                    contract = RecordedContract.model_validate(raw)
                    if contract.name in contracts and contracts[contract.name] != contract:
                        raise ReplayMismatch("source_contract_changed")
                    contracts[contract.name] = contract
            evidence.append((step, inp, out, redacted))

        def fields(schema):
            result = []
            for name, field in schema.get("properties", {}).items():
                supported = {"string", "number", "integer", "boolean", "object", "array"}
                variants = [field, *field.get("anyOf", ())]
                kind = "unknown"
                for variant in variants:
                    declared = variant.get("type")
                    choices = declared if isinstance(declared, list) else [declared]
                    match = next(
                        (v for v in choices if isinstance(v, str) and v in supported), None
                    )
                    if match:
                        kind = match
                        break
                result.append(
                    RecordingField(
                        name=name,
                        type=kind,
                        required=name in schema.get("required", ()),
                        nonsemantic=field.get("x-recording-nonsemantic") is True,
                    )
                )
            return tuple(result)

        items = []
        for step, inp, out, redacted in evidence:
            kind = out.get("kind")
            if kind == "retrieval":
                contract = retrieval_contract()
                schema = {
                    "properties": {"query": {"type": "string"}, "sources": {"type": "array"}},
                    "required": ["query", "sources"],
                }
            elif kind == "tool":
                name = inp.get("request", {}).get("tool_call", {}).get("name")
                contract = contracts.get(name)
                if contract is None:
                    logger.warning(
                        "recording candidate contract missing: step=%s output_kind=%s tool_present=%s kinds=%s captured_count=%s",
                        step.step_id,
                        kind,
                        name is not None,
                        tuple(item[2].get("kind") for item in evidence),
                        len(contracts),
                    )
                    raise ReplayMismatch("contract_unavailable")
                schema = ToolResult.model_json_schema()
            else:
                continue
            items.append(
                RecordingCandidate(
                    tool=contract.name,
                    step_id=step.step_id,
                    activity_id=step.activity_id,
                    effect=contract.policy.effect.value,
                    result_fields=fields(schema),
                    argument_fields=fields(contract.arguments_schema),
                    requires_argument_replacement=redacted,
                )
            )
        start = position["offset"] if position else 0
        if type(start) is not int or not 0 <= start <= len(items):
            raise ValueError("invalid_cursor")
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
        return RecordingCandidatePage(
            run_id=run_id,
            at=at,
            items=tuple(items[start : start + limit]),
            next_cursor=encode(self.cursor_secret, context, {"at": at, "offset": start + limit})
            if start + limit < len(items)
            else None,
        )

    async def create(
        self, scope, principal, run_id: UUID, allowed_fields, replacements=None, *, request_id
    ):
        if not request_id.strip() or len(request_id) > 255:
            raise ValueError("request_id_required")
        # Typed selections include explicit per-tool replacements and rule exclusions.
        selections = tuple(RecordingSelection.model_validate(value) for value in allowed_fields)
        if (
            not selections
            or len({value.tool for value in selections}) != len(selections)
            or replacements
        ):
            raise ValueError("invalid_recording_selection")
        await self.source.views.get_view(scope, run_id)  # metadata only, no result body here
        serialized = [value.model_dump(mode="json") for value in selections]
        digest = fingerprint("recording.create", [str(run_id), serialized])
        async with self.uow_factory(self.auth(scope, principal, request_id)) as uow:
            receipts = uow.evaluation_dataset
            await receipts.authorize(scope, principal, write=True)
            await receipts.lock_request(scope, request_id)
            previous = await receipts.receipt(scope, request_id, digest)
            if previous:
                return public_job(previous)
            result = RecordingJob(id=uuid4(), source_run_id=run_id)
            await uow.evaluation_recording.create(scope, result, serialized, principal)
            value = result.model_dump(mode="json") | {
                "operation": "recording.create",
                "audit_resource_id": str(result.id),
            }
            await receipts.save_receipt(scope, request_id, digest, value)
            await uow.audit.add_evaluation(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.recording.create",
                    resource_type="evaluation_recording",
                    resource_id=str(result.id),
                    request_id=request_id,
                    metadata={"revision": 1},
                ),
                authorization=self.auth(scope, principal, request_id),
            )
            await receipts.authorize(scope, principal, write=True)
            await uow.commit()
            return result

    async def status(self, scope, principal, job_id):
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            row = await uow.evaluation_recording.job(scope, job_id)
        await self.source.views.get_view(scope, row["source_run_id"])
        return public_job(row)

    async def result(self, scope, principal, job_id):
        from app.application.evaluation.recording_authority import validate_recording
        from app.domain.evaluation.errors import DatasetConflict

        status = await self.status(scope, principal, job_id)
        if status.status != "ready" or status.result_version is None:
            raise DatasetConflict("recording_not_ready")
        async with self.uow_factory(self.auth(scope, principal)) as uow:
            manifest = await validate_recording(uow, scope, principal, status.result_version)
            # Explicit metadata-only result. Bodies, storage keys, args, pins remain private.
            return {
                "version_id": str(manifest.id),
                "revision": manifest.revision,
                "slot_count": len(manifest.slots),
                "tool_names": [c.name for c in manifest.contracts],
            }
