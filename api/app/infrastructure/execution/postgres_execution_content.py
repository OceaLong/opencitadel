"""Kernel-only capture and transaction-bound immutable public content production."""

import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import text

from app.application.dto.execution_view import CitationReference, ContentReference
from app.application.execution.content_sanitization import sanitize_content
from app.application.execution.view_facts import ProjectionFact, attempt_key
from app.domain.models.artifact_provenance import ArtifactProducer
from app.domain.models.resource_pin import ResourceUnavailable
from app.infrastructure.repositories.db_resource_pin_repository import scope_params
from app.infrastructure.security.db_authorization import configure_session_authorization


def content_reference(row):
    return ContentReference(
        content_id=str(row["content_id"]),
        media_type="application/json",
        byte_length=row["byte_length"],
        truncated=False,
        availability="available",
    )


class ExecutionContentWriter:
    def __init__(self, *, session_factory, authorization, objects):
        self.sessions = session_factory
        self.authorization = authorization
        self.objects = objects

    async def record(self, producer, *, command_id, phase, value, citations=(), attachment_ids=()):
        sanitized = sanitize_content(value)
        body = json.dumps(
            sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        digest = hashlib.sha256(body.encode()).hexdigest()
        p = {
            **scope_params(producer.scope),
            "content": uuid4(),
            "command": command_id,
            "run": producer.run_id,
            "activity": producer.activity_id,
            "generation": producer.generation,
            "claim": producer.claim_generation,
            "phase": phase,
            "body": body,
            "digest": digest,
            "length": len(body.encode()),
            "redacted": sanitized != value,
            "citations": "[]",
        }
        async with self.sessions() as session:
            await configure_session_authorization(session, self.authorization)
            # Actual persisted claim authority, never model-supplied activity IDs.
            valid = await session.scalar(
                text("""SELECT 1 FROM execution_activity_tasks
              WHERE activity_id=:activity AND aggregate_id=CAST(CAST(:run AS uuid) AS text) AND request_generation=:generation
              AND claim_generation=:claim AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"""),
                p,
            )
            if not valid:
                raise ResourceUnavailable("snapshot claim unavailable")
            from app.domain.models.knowledge_citation import (
                KnowledgeCitation,
                deduplicate_citations,
            )
            from app.infrastructure.repositories.db_knowledge_base_repository import (
                DBKnowledgeBaseRepository,
            )

            refs = []
            repo = DBKnowledgeBaseRepository(session)
            for citation in deduplicate_citations(list(citations)):
                if not isinstance(citation, KnowledgeCitation) or not citation.knowledge_base_id:
                    continue  # Legacy citations have no owning authority; never guess it.
                kb = citation.knowledge_base_id
                if await repo.get_kb(kb, scope=producer.scope) is None:
                    raise ResourceUnavailable("citation owner unavailable")
                matches = await repo.get_chunks_by_ids_for_version(
                    kb, citation.version_id, [citation.chunk_id]
                )
                if not any(
                    r.chunk.id == citation.chunk_id
                    and r.chunk.kb_id == kb
                    and r.chunk.doc_id == citation.doc_id
                    and r.document_revision_id == citation.document_revision_id
                    and r.chunk.page_no == citation.page_no
                    for r in matches
                ):
                    raise ResourceUnavailable("citation source unavailable")
                identity = json.dumps(citation.model_dump(), sort_keys=True)
                refs.append(
                    CitationReference(
                        citation_id=str(
                            uuid5(NAMESPACE_URL, str(command_id) + ":" + phase + ":" + identity)
                        ),
                        availability="available",
                        **citation.model_dump(),
                    ).model_dump(mode="json")
                )
            from app.infrastructure.repositories.db_file_repository import DBFileRepository

            for file_id in dict.fromkeys(attachment_ids):
                file = await DBFileRepository(session).get_by_id(file_id, scope=producer.scope)
                if file is None or not file.content_available:
                    raise ResourceUnavailable("attachment unavailable")
                refs.append(
                    CitationReference(
                        citation_id=str(uuid5(NAMESPACE_URL, str(command_id) + ":file:" + file.id)),
                        resource_kind="file",
                        file_id=file.id,
                        content_digest=file.content_digest,
                        object_identity=file.object_identity,
                        availability="available"
                        if file.content_digest and file.object_identity
                        else "unavailable",
                    ).model_dump(mode="json")
                )
            p["citations"] = json.dumps(refs)
            await session.execute(
                text("""INSERT INTO execution_public_content(content_id,command_id,run_id,activity_id,generation,claim_generation,phase,body,content_digest,byte_length,redacted,citation_refs,owner_user_id,team_id,created_by)
                VALUES (:content,:command,:run,:activity,:generation,:claim,:phase,:body,:digest,:length,:redacted,CAST(:citations AS jsonb),:owner,:team,'execution-worker')
                ON CONFLICT(scope_key,command_id,phase) DO NOTHING"""),
                p,
            )
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM execution_public_content WHERE scope_key=:scope AND command_id=:command AND phase=:phase"
                        ),
                        p,
                    )
                )
                .mappings()
                .one()
            )
            if (
                row["content_digest"] != digest
                or row["run_id"] != producer.run_id
                or row["activity_id"] != producer.activity_id
                or row["claim_generation"] != producer.claim_generation
                or row["citation_refs"] != refs
            ):
                raise ResourceUnavailable("immutable content capture differs")
            await session.commit()
            return content_reference(row)

    async def prepare(self, claim, command_id, command_type, payload, *, citations=()):
        if command_type == "MarkActivityCallStarted":
            phase = "input"
            if claim.request.input_ref:
                value = await self.objects.load_input(
                    key=claim.request.input_ref, expected_digest=claim.request.input_digest
                )
            else:
                value = {}
            value = {"context": value, "request": dict(claim.request.input_payload)}
        elif command_type == "CompleteActivity" and payload.get("result_ref"):
            phase = "output"
            value = await self.objects.load_result(payload["result_ref"])
        else:
            return
        from app.domain.models.scope import OwnerScope

        scope = (
            OwnerScope.team(claim.owner_user_id or "execution-worker", claim.team_id)
            if claim.team_id
            else OwnerScope.personal(claim.owner_user_id)
        )
        producer = ArtifactProducer(
            scope=scope,
            run_id=claim.request.aggregate_id,
            activity_id=claim.request.activity_id,
            generation=claim.request.generation,
            claim_generation=claim.claim_generation,
        )
        attachments = value.get("context", {}).get("attachments", []) if phase == "input" else []
        if not isinstance(attachments, list) or len(attachments) > 10:
            raise ResourceUnavailable("invalid attachment selection")
        attachment_ids = [
            item["file_id"]
            for item in attachments
            if isinstance(item, dict) and isinstance(item.get("file_id"), str)
        ]
        await self.record(
            producer,
            command_id=command_id,
            phase=phase,
            value=value,
            citations=citations,
            attachment_ids=attachment_ids,
        )


async def bind_new_content_events(session, events):
    """Called ONLY after append in the accepting command transaction.

    A duplicate command never executes this insertion. A later snapshot cannot
    retroactively attach to an old event even when its projector has not run.
    """
    for event in events:
        phase = {"ActivityCallStarted": "input", "ActivityCompleted": "output"}.get(
            event.event_type
        )
        if not phase:
            continue
        p = event.public_payload
        if p.get("claim_generation") is None:
            continue
        await session.execute(
            text("""INSERT INTO execution_content_bindings(event_id,phase,content_id,run_id,step_id,formal_position,owner_user_id,team_id,created_by)
          SELECT :event,phase,content_id,run_id,:step,:position,owner_user_id,team_id,'execution-kernel'
          FROM execution_public_content WHERE command_id=:command AND phase=:phase AND run_id=:run
          AND activity_id=:activity AND generation=:generation AND claim_generation=:claim
          AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"""),
            {
                "event": event.event_id,
                "phase": phase,
                "step": attempt_key(str(p["activity_id"]), p["generation"], p["claim_generation"]),
                "position": event.position,
                "command": event.causation_id,
                "run": UUID(event.stream_id),
                "activity": UUID(str(p["activity_id"])),
                "generation": p["generation"],
                "claim": p["claim_generation"],
                "owner": event.owner_user_id,
                "team": event.team_id,
            },
        )


async def project_content(session, *, event, observation):
    rows = (
        (
            await session.execute(
                text("""SELECT c.*,b.step_id FROM execution_content_bindings b JOIN execution_public_content c USING(content_id)
      WHERE b.event_id=:event AND b.run_id=:run AND b.formal_position=:position
      AND b.owner_user_id IS NOT DISTINCT FROM :owner AND b.team_id IS NOT DISTINCT FROM :team"""),
                {
                    "event": event.event_id,
                    "run": UUID(event.stream_id),
                    "position": event.position,
                    "owner": event.owner_user_id,
                    "team": event.team_id,
                },
            )
        )
        .mappings()
        .all()
    )
    patches = []
    for row in rows:
        key = row["phase"] + "_ref"
        ref = content_reference(row).model_dump(mode="json")
        patch = {key: ref}
        if row["citation_refs"]:
            previous = await session.scalar(
                text(
                    "SELECT citation_refs FROM execution_view_steps WHERE run_id=:run AND step_id=:step"
                ),
                {"run": UUID(event.stream_id), "step": row["step_id"]},
            )
            merged = {r["citation_id"]: r for r in [*(previous or []), *row["citation_refs"]]}
            patch["citation_refs"] = list(merged.values())
            await session.execute(
                text(
                    "UPDATE execution_view_steps SET citation_refs=CAST(:refs AS jsonb) WHERE run_id=:run AND step_id=:step"
                ),
                {
                    "refs": json.dumps(patch["citation_refs"]),
                    "run": UUID(event.stream_id),
                    "step": row["step_id"],
                },
            )
        patches.append(
            ProjectionFact(
                event.position, 0, None, "step", row["step_id"], patch, "formal"
            ).playback_patch()
        )
        await session.execute(
            text(f"""UPDATE execution_view_steps SET {key}=CAST(:reference AS jsonb),projection_revision=:revision
            WHERE run_id=:run AND step_id=:step AND owner_user_id IS NOT DISTINCT FROM :owner AND team_id IS NOT DISTINCT FROM :team"""),
            {
                "reference": json.dumps(ref),
                "revision": observation.projection_revision,
                "run": UUID(event.stream_id),
                "step": row["step_id"],
                "owner": event.owner_user_id,
                "team": event.team_id,
            },
        )
    if patches:
        observation.public_payload = {
            **observation.public_payload,
            "facts": [*observation.public_payload["facts"], *patches],
        }
