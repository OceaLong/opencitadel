"""Exact replay consumption. The current authority gate precedes every result read."""

import hashlib

from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import RecordedToolResult, recording_key


class ReplayAdapter:
    def __init__(self, authority, objects):
        self.authority, self.objects = authority, objects

    async def match(
        self, context, tool, contract, args, branch, ordinal, *, parallel_group=""
    ) -> RecordedToolResult:
        async with self.authority.open(context) as access:
            manifest, repo, scope = access.manifest, access.repo, access.scope
            descriptors = [
                item for item in manifest.contracts if item.name == tool and item.digest == contract
            ]
            if len(descriptors) != 1:
                raise ReplayMismatch("contract_changed")
            descriptor = descriptors[0]
            # Approval uses the current run/activity, never recorded approval state.
            await self.authority.approve(access, context, descriptor.policy)
            candidates = [
                slot
                for slot in manifest.slots
                if (slot.tool, slot.contract_digest, slot.branch, slot.parallel_group, slot.ordinal)
                == (tool, contract, branch, parallel_group, ordinal)
            ]
            if len(candidates) != 1:
                raise ReplayMismatch("slot_unmatched")
            slot = candidates[0]
            try:
                normalized = slot.rule.normalize(args, descriptor.arguments_schema)
            except ValueError as error:
                raise ReplayMismatch("arguments_invalid") from error
            key = recording_key(
                tool,
                contract,
                normalized,
                branch,
                ordinal,
                parallel_group=parallel_group,
                rule_version=slot.rule.version,
            )
            if key != slot.match_key:
                raise ReplayMismatch("arguments_mismatch")
            run_id, activity_id = context.run.run_id, context.activity_id
            if activity_id is None:
                raise ReplayMismatch("call_identity_missing")
            await repo.lock_call(scope, run_id)
            previous = await repo.consumed(scope, run_id, activity_id)
            if previous and (
                previous["match_key"],
                previous["slot_id"],
                previous["version_id"],
            ) != (key, slot.id, manifest.id):
                raise ReplayMismatch("call_identity_changed")
            obj = await repo.object(scope, slot.object_id)
            if obj["digest"] != slot.result_digest or obj["size_bytes"] != slot.result_bytes:
                raise ReplayMismatch("recorded_object_changed")
            try:
                body = await self.objects.get_bytes(obj["storage_key"])
            except (KeyError, FileNotFoundError) as error:
                raise ReplayMismatch("recorded_object_missing") from error
            # Transport errors retain their infrastructure semantics; no real fallback.
            if (
                len(body) != slot.result_bytes
                or hashlib.sha256(body).hexdigest() != slot.result_digest
            ):
                raise ReplayMismatch("recorded_object_changed")
            if not previous:
                await repo.consume(scope, run_id, activity_id, manifest.id, slot)
            await access.uow.commit()
            return RecordedToolResult(
                result_ref=obj["storage_key"],
                simulated_effect=slot.simulated_effect,
                recording_revision=manifest.revision,
            )
