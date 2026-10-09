"""Exact terminal result reference → accepted F06 snapshots, with current user authority.

Kernel metadata and user content transactions never overlap. No private result body,
preview, mutable message, or model-provided simulation flag is an authority source.
"""

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import text

from app.application.execution import activity_types
from app.domain.evaluation.rule_engine import (
    MISSING,
    ArtifactEvidence,
    RuleEvidence,
    corroborate_simulated_effect,
)
from app.domain.evaluation.scoring import RecordingEvidence
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
from app.domain.services.content_source_authority import content_source_available
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


@dataclass(frozen=True)
class ScoringEvidence:
    subject: object = MISSING
    content_id: str | None = None
    evidence: RuleEvidence = field(default_factory=RuleEvidence)
    unavailable_reason: str | None = None
    resources: tuple[ResourceIdentity, ...] = ()
    sources: tuple[ArtifactEvidence, ...] = ()


class RuleEvidenceReader:
    def __init__(self, uow_factory, *, content_factory):
        self.uow_factory, self.content_factory = uow_factory, content_factory

    async def _selection(self, scope, principal, candidate):
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            projection = await work.evaluation_score.eligible(scope, principal, candidate)
            state = projection["state"]
            final = state.get("result_ref")
            matches = [r for r in state.get("activity_results", ()) if final and r[2] == final]
            if len(matches) != 1:
                return None
            activity, generation = matches[0][:2]
            if not any(
                str(r[0]) == str(activity)
                and r[1] == activity_types.MODEL_CALL
                and r[2] == generation
                for r in state.get("requested_activities", ())
            ):
                return None
            position = await work.db_session.scalar(
                text(
                    """SELECT position FROM execution_events
                    WHERE stream_type='run' AND stream_id=:run
                    AND stream_version<=:revision AND event_type='RunCompleted'
                    AND owner_user_id IS NOT DISTINCT FROM :owner
                    AND team_id IS NOT DISTINCT FROM :team
                    ORDER BY stream_version DESC LIMIT 1"""
                ),
                params(scope, run=str(candidate.run_id), revision=candidate.run_revision),
            )
            if position is None:
                return None
            bindings = (
                (
                    await work.db_session.execute(
                        text("""SELECT c.content_id,c.content_digest,c.activity_id,c.generation,b.step_id,b.formal_position,c.citation_refs FROM execution_public_content c
              JOIN execution_content_bindings b ON b.scope_key=c.scope_key AND b.content_id=c.content_id
              WHERE b.scope_key=:scope AND b.run_id=:run AND b.phase='output' AND b.formal_position<=:position ORDER BY b.formal_position"""),
                        params(scope, run=candidate.run_id, position=position),
                    )
                )
                .mappings()
                .all()
            )
            selected = [
                b
                for b in bindings
                if str(b["activity_id"]) == str(activity) and b["generation"] == generation
            ]
            if len(selected) != 1:
                return None
            artifacts = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT artifact_id,version,content_digest,availability,citation_refs FROM artifact_version_provenance WHERE scope_key=:scope AND producer_run_id=:run AND binding_status='bound' AND boundary<=:position"
                        ),
                        params(scope, run=candidate.run_id, position=position),
                    )
                )
                .mappings()
                .all()
            )
            binding = await work.evaluation_recording.binding(scope, candidate.run_id)
            if state.get("source_entity_type") == "evaluation_recorded_case" and binding is None:
                return None
            recording = None
            if binding:
                manifest = await work.evaluation_recording.version(scope, binding["version_id"])
                consumed = (
                    (
                        await work.db_session.execute(
                            text(
                                "SELECT activity_id,slot_id FROM evaluation_replay_ledger WHERE scope_key=:scope AND run_id=:run AND version_id=:version"
                            ),
                            params(scope, run=candidate.run_id, version=manifest.id),
                        )
                    )
                    .mappings()
                    .all()
                )
                coverage = await work.evaluation_recording.coverage(scope, candidate.run_id)
                recording = (
                    binding,
                    manifest,
                    tuple(dict(r) for r in consumed),
                    dict(coverage),
                    state,
                )
            return (
                dict(selected[0]),
                tuple(
                    {
                        **dict(r),
                        "source_eligible": any(
                            str(a[0]) == str(r["activity_id"])
                            and a[1] in {activity_types.TOOL_CALL, activity_types.RETRIEVAL_SEARCH}
                            and a[2] == r["generation"]
                            for a in state.get("requested_activities", ())
                        ),
                    }
                    for r in bindings
                ),
                tuple(dict(r) for r in artifacts),
                recording,
            )

    async def _body(self, scope, principal, candidate, binding):
        auth = AuthorizationContext.for_principal(principal, scope=scope)
        async with self.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            meta = await work.execution_content.get_snapshot(
                scope,
                str(binding["content_id"]),
                candidate.run_id,
                binding["step_id"],
                binding["formal_position"],
                include_body=False,
            )
            if meta is None:
                raise ResourceUnavailable("score_output_unavailable")
            for ref in meta["citation_refs"]:
                if not await content_source_available(
                    scope, ref, file_repository=work.file, knowledge_repository=work.knowledge_base
                ):
                    raise ResourceUnavailable("score_source_unavailable")
            row = await work.execution_content.get_snapshot(
                scope,
                str(binding["content_id"]),
                candidate.run_id,
                binding["step_id"],
                binding["formal_position"],
            )
            if (
                row is None
                or row["redacted"]
                or hashlib.sha256(row["body"].encode()).hexdigest() != row["content_digest"]
            ):
                raise ResourceUnavailable("score_output_integrity_unavailable")
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            from app.application.execution.content_sanitization import sanitize_content

            try:
                body = json.loads(row["body"])
                if not isinstance(body, dict) or sanitize_content(body) != body:
                    raise ResourceUnavailable("score_output_integrity_unavailable")
                return body
            except (ValueError, RecursionError) as error:
                raise ResourceUnavailable("score_output_integrity_unavailable") from error

    async def read(self, scope, principal, candidate, *, resources):
        selected = await self._selection(scope, principal, candidate)
        if selected is None:
            return ScoringEvidence(unavailable_reason="terminal_result_unavailable")
        final, bindings, artifacts, recording = selected
        try:
            body = await self._body(scope, principal, candidate, final)
            message = body.get("message", {})
            if (
                body.get("kind") != "model"
                or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str)
                or message.get("tool_calls")
            ):
                return ScoringEvidence(unavailable_reason="terminal_result_invalid")
        except ResourceUnavailable:
            return ScoringEvidence(unavailable_reason="terminal_content_unavailable")
        auth = AuthorizationContext.for_principal(principal, scope=scope)
        citations, available, unavailable = [], [], False
        # Verify canonical fixed-version citations, never scan answer text for identities.
        async with self.uow_factory(auth) as work:
            await work.evaluation_dataset.authorize(scope, principal, write=False)
            for binding in bindings:
                for ref in binding["citation_refs"]:
                    if await content_source_available(
                        scope,
                        ref,
                        file_repository=work.file,
                        knowledge_repository=work.knowledge_base,
                    ):
                        identity = (
                            ResourceIdentity(
                                resource_kind="file",
                                resource_id=ref["file_id"],
                                resource_version=ref["content_digest"],
                            )
                            if ref.get("resource_kind") == "file"
                            else ResourceIdentity(
                                resource_kind="knowledge_base",
                                resource_id=ref["knowledge_base_id"],
                                resource_version=ref["version_id"],
                            )
                        )
                        citations.append(identity)
                        available.append(identity)
                    else:
                        unavailable = True
            for resource in resources:
                try:
                    await work.resource_pins.resolve(scope, resource)
                    available.append(resource)
                except ResourceUnavailable:
                    unavailable = True
        artifact_evidence = []
        content = self.content_factory(auth)
        for artifact in artifacts:
            if artifact["availability"] != "available" or content is None:
                unavailable = True
                continue
            parts, cursor, seen = [], None, set()
            try:
                while True:
                    # Recheck the original principal before each fixed-version page.
                    # Release the UoW before the content reader borrows its own one.
                    async with self.uow_factory(auth) as work:
                        await work.evaluation_dataset.authorize(scope, principal, write=False)
                    page = await content.read_artifact(
                        scope, artifact["artifact_id"], artifact["version"], cursor=cursor
                    )
                    if page.availability != "available" or page.content is None:
                        raise ResourceUnavailable("artifact_unavailable")
                    parts.append(page.content)
                    if sum(len(p.encode()) for p in parts) > 20 * 1024 * 1024:
                        raise ResourceUnavailable("artifact_too_large")
                    if not page.truncated:
                        if page.next_cursor:
                            raise ResourceUnavailable("artifact_incomplete")
                        break
                    if not page.next_cursor or page.next_cursor in seen:
                        raise ResourceUnavailable("artifact_incomplete")
                    cursor = page.next_cursor
                    seen.add(cursor)
                data = "".join(parts)
                if (
                    artifact["content_digest"]
                    != "sha256:" + hashlib.sha256(data.encode()).hexdigest()
                ):
                    raise ResourceUnavailable("artifact_changed")
                try:
                    structure = json.loads(data)
                except ValueError:
                    structure = data
                artifact_evidence.append(
                    ArtifactEvidence(
                        ResourceIdentity(
                            resource_kind="artifact",
                            resource_id=artifact["artifact_id"],
                            resource_version=str(artifact["version"]),
                        ),
                        "web" if page.content_type == "text/html" else "doc",
                        structure,
                    )
                )
            except ResourceUnavailable:
                unavailable = True
        simulated, revision, recording_evidence = False, None, None
        verified = [
            ResourceIdentity(
                resource_kind="execution_content",
                resource_id=str(final["content_id"]),
                resource_version=final["content_digest"],
            )
        ]
        verified.extend(available)
        verified.extend(a.resource for a in artifact_evidence)
        if recording:
            binding, manifest, consumed, coverage, state = recording
            admission = binding["admission"]
            if (
                binding["principal"] != principal.model_dump(mode="json")
                or admission.get("config_version_id") != str(candidate.config_version_id)
                or admission.get("source_entity_type") != state.get("source_entity_type")
                or admission.get("source_entity_id") != state.get("source_entity_id")
                or admission.get("purpose") != "evaluation_subject"
                or admission.get("policy_digest")
                != state.get("policy_snapshot", {}).get("snapshot_digest")
            ):
                return ScoringEvidence(unavailable_reason="recording_binding_unavailable")
            from app.application.evaluation.recording_authority import validate_recording
            from app.domain.evaluation.errors import ReplayMismatch

            try:
                async with self.uow_factory(auth) as work:
                    await validate_recording(work, scope, principal, manifest.id)
                    redacted_content = {
                        str(content_id)
                        for content_id in (
                            await work.db_session.execute(
                                text("""SELECT DISTINCT c.content_id FROM execution_public_content c
                  JOIN execution_content_bindings b ON b.scope_key=c.scope_key AND b.content_id=c.content_id
                  WHERE c.scope_key=:scope AND b.run_id=:run AND c.redacted"""),
                                params(scope, run=manifest.source_run_id),
                            )
                        ).scalars()
                    }
            except (ReplayMismatch, ResourceUnavailable):
                return ScoringEvidence(unavailable_reason="recording_source_unavailable")
            # Recording retains redacted inputs and outputs for replay integrity,
            # but neither is readable material supplied to the judge.
            verified.extend(
                pin
                for pin in manifest.pins
                if pin.resource_kind != "execution_content"
                or pin.resource_id not in redacted_content
            )
            simulated_ids = []
            for ledger in consumed:
                slot = next((s for s in manifest.slots if s.id == ledger["slot_id"]), None)
                contract = next(
                    (
                        c
                        for c in manifest.contracts
                        if slot is not None and c.digest == slot.contract_digest
                    ),
                    None,
                )
                matched = [b for b in bindings if b["activity_id"] == ledger["activity_id"]]
                if slot is None or contract is None or len(matched) != 1:
                    unavailable = True
                    continue
                from app.domain.models.tool_policy import ToolEffect

                try:
                    output = await self._body(scope, principal, candidate, matched[0])
                    corroborated = corroborate_simulated_effect(
                        output,
                        simulated_slot=slot.simulated_effect,
                        write_effect=contract.policy.effect != ToolEffect.READ_ONLY,
                        revision=manifest.revision,
                    )
                except (ResourceUnavailable, ValueError):
                    unavailable = True
                    continue
                if corroborated:
                    simulated = True
                    simulated_ids.append(ledger["activity_id"])
                verified.append(
                    ResourceIdentity(
                        resource_kind="execution_content",
                        resource_id=str(matched[0]["content_id"]),
                        resource_version=matched[0]["content_digest"],
                    )
                )
            revision = str(manifest.revision)
            recording_evidence = RecordingEvidence(
                version_id=manifest.id,
                revision=manifest.revision,
                total=coverage["total"],
                consumed=coverage["consumed"],
                mismatches=coverage["mismatches"],
                simulated_activity_ids=tuple(simulated_ids),
            )
            if not recording_evidence.quality_eligible:
                return ScoringEvidence(unavailable_reason="recording_mismatch")
            if unavailable:
                # Incomplete replay corroboration cannot become execution-quality evidence.
                return ScoringEvidence(unavailable_reason="recording_evidence_unavailable")
        sources = []
        for binding in bindings:
            if (
                not binding.get("source_eligible")
                or not binding["citation_refs"]
                or (
                    recording_evidence
                    and binding["activity_id"] in recording_evidence.simulated_activity_ids
                )
            ):
                continue
            try:
                source = await self._body(scope, principal, candidate, binding)
            except ResourceUnavailable:
                continue
            resource = ResourceIdentity(
                resource_kind="execution_content",
                resource_id=str(binding["content_id"]),
                resource_version=binding["content_digest"],
            )
            verified.append(resource)
            sources.append(ArtifactEvidence(resource, "fixed_source", source))
        return ScoringEvidence(
            sources=tuple(sources),
            subject=message["content"],
            content_id=str(final["content_id"]),
            resources=tuple(verified),
            evidence=RuleEvidence(
                available=not unavailable,
                citations=tuple(citations),
                available_sources=tuple(available),
                artifacts=tuple(artifact_evidence),
                simulated=simulated,
                recording_revision=revision,
                recording=recording_evidence,
            ),
        )
