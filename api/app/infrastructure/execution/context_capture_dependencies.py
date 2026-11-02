"""Keep copied context bodies dependent on their current original resource authority."""

from uuid import UUID

from sqlalchemy import select, text

from app.application.execution import activity_types
from app.domain.execution.context_models import ContextCompilation, context_digest
from app.domain.execution.context_resources import ContextResourceEvidence, memory_content_digest
from app.domain.models.resource_pin import ResourceUnavailable
from app.infrastructure.models.memory_entry import MemoryEntryORM


class ContextCaptureUnavailable(ResourceUnavailable):
    """Known revoked copied content; it must not prevent formal Activity settlement."""


async def validate_captured_dependencies(session, scope, dependencies):
    from types import SimpleNamespace

    from app.infrastructure.execution.context_resource_authority import validate_resource_evidence
    from app.infrastructure.repositories.db_knowledge_base_repository import (
        DBKnowledgeBaseRepository,
    )
    from app.infrastructure.repositories.db_knowledge_version_repository import (
        DBKnowledgeVersionRepository,
    )

    if not isinstance(dependencies, list) or len(dependencies) > 4096:
        raise ResourceUnavailable("copied context dependencies unavailable")
    for dependency in dependencies:
        try:
            kind = dependency["kind"]
            if kind == "memory":
                expected = dependency["entry"]
                query = select(MemoryEntryORM).where(MemoryEntryORM.id == expected["entry_id"])
                query = (
                    query.where(MemoryEntryORM.team_id == scope.team_id)
                    if scope.team_id
                    else query.where(
                        MemoryEntryORM.team_id.is_(None),
                        MemoryEntryORM.owner_user_id == scope.user_id,
                    )
                )
                row = await session.scalar(query.with_for_update(read=True))
                if (
                    row is None
                    or (
                        row.scope != "global"
                        and (
                            row.scope != "session" or row.session_id != dependency.get("session_id")
                        )
                    )
                    or memory_content_digest(row.to_domain()) != expected["content_digest"]
                ):
                    raise ResourceUnavailable("copied memory unavailable")
            elif kind == "knowledge":
                version = dependency["version"]
                await validate_resource_evidence(
                    SimpleNamespace(
                        db_session=session,
                        knowledge_base=DBKnowledgeBaseRepository(session),
                        knowledge_version=DBKnowledgeVersionRepository(session),
                    ),
                    scope,
                    {
                        "resource_bindings": [
                            {
                                "resource_kind": "knowledge_base",
                                "resource_id": version["knowledge_base_id"],
                                "version_id": version["version_id"],
                            }
                        ]
                    },
                    {"knowledge": [version]},
                )
            elif kind == "skill":
                from app.infrastructure.execution.context_skill_authority import (
                    validate_skill_version,
                )

                await validate_skill_version(
                    session, scope, dependency["skill_id"], dependency["content_digest"]
                )
            elif kind == "judge":
                from app.infrastructure.repositories.db_evaluation_judge_repository import (
                    DBEvaluationJudgeRepository,
                )

                authority = DBEvaluationJudgeRepository.from_session(session)
                intent = await authority.get(scope, UUID(dependency["run_id"]))
                if (
                    intent is None
                    or context_digest(intent["materials"]) != dependency["materials_digest"]
                ):
                    raise ResourceUnavailable("copied judge materials changed")
                await authority.current(scope, intent, lock=True)
            elif kind == "file":
                from app.domain.execution.context_resources import ContextFileEvidence
                from app.infrastructure.execution.context_file_authority import (
                    validate_file_version,
                )

                await validate_file_version(
                    session, scope, ContextFileEvidence.model_validate(dependency["file"])
                )
            else:
                raise ResourceUnavailable("unknown copied context dependency")
        except ResourceUnavailable:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ResourceUnavailable("copied context dependency unavailable") from error


async def capture_context_dependencies(session, task, value, *, phase, objects, scope):
    from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader

    collected = []
    # Capture selected instructions on both input and output; copied results must
    # never outlive the original skill's current visibility/version permission.
    if task.definition_digest is not None and task.request_ref:
        original = await objects.load_input_bounded(
            key=task.request_ref, expected_digest=task.request_digest, limit=262144
        )
        if original.get("_context_skill") is not None:
            from app.domain.execution.context_resources import ContextSkillEvidence

            evidence = ContextSkillEvidence.model_validate(original["_context_skill"])
            if evidence.skill_id != original.get("skill_id"):
                raise ResourceUnavailable("copied skill identity differs")
            collected.append(
                {
                    "kind": "skill",
                    "skill_id": evidence.skill_id,
                    "content_digest": evidence.content_digest,
                }
            )
        if original.get("_context_files"):
            from app.domain.execution.context_resources import ContextFileEvidence

            collected.extend(
                {
                    "kind": "file",
                    "file": ContextFileEvidence.model_validate(item).model_dump(mode="json"),
                }
                for item in original["_context_files"]
            )
    # Only kernel-produced retrieval/tool output can introduce resource evidence.
    if phase == "output" and task.activity_type in {
        activity_types.RETRIEVAL_SEARCH,
        activity_types.TOOL_CALL,
    }:
        raw = value.get("resource_evidence")
        if raw is not None:
            evidence = ContextResourceEvidence.model_validate(raw)
            if evidence.unresolved_sources:
                raise ResourceUnavailable("unresolved copied context resources")
            if evidence.memory and evidence.memory.entries:
                original = await objects.load_input(
                    key=task.request_ref, expected_digest=task.request_digest
                )
                if evidence.memory.query_digest != context_digest(original.get("message")):
                    raise ResourceUnavailable("copied memory query differs")
                collected.extend(
                    {
                        "kind": "memory",
                        "entry": entry.model_dump(mode="json"),
                        "session_id": original.get("session_id"),
                    }
                    for entry in evidence.memory.entries
                )
            collected.extend(
                {"kind": "knowledge", "version": version.model_dump(mode="json")}
                for version in evidence.knowledge
            )
    prepared = None
    if task.activity_type == activity_types.CONTEXT_PREPARE and phase == "output":
        prepared = value
    elif task.activity_type in {activity_types.MODEL_CALL, activity_types.CONTEXT_COMPACT}:
        request = task.request_payload
        ref = request.get("context_ref") or request.get("prepare_ref")
        if ref:
            state = (
                await PostgresVerifiedRunLoader(session).load(
                    task.aggregate_id,
                    owner_scope=(None if scope.team_id else scope.user_id, scope.team_id),
                )
            ).replay.state
            if not any(
                result == ref
                and generation == task.request_generation
                and (activity, activity_types.CONTEXT_PREPARE, generation)
                in state.requested_activities
                and (activity, "succeeded", generation) in state.settled_activities
                for activity, generation, result, _, _ in state.activity_results
            ):
                raise ResourceUnavailable("copied preparation is not formally complete")
            prepared = await objects.load_context_document(ref)
    if prepared is not None:
        compilation = ContextCompilation.model_validate(prepared.get("compilation"))
        refs = (
            compilation.manifest.original_closure
            if compilation.manifest
            else tuple(ref for chunk in compilation.compaction.chunks for ref in chunk.sources)
            if compilation.compaction
            else ()
        )
        if task.activity_type == activity_types.CONTEXT_COMPACT and compilation.compaction:
            refs = next(
                chunk.sources
                for chunk in compilation.compaction.chunks
                if chunk.ordinal == task.request_payload["ordinal"]
            )
        identities = {}
        for ref in refs:
            if ref.source_id.startswith(("judge:", "judge-platform:")):
                from app.infrastructure.repositories.db_evaluation_judge_repository import (
                    DBEvaluationJudgeRepository,
                )

                intent = await DBEvaluationJudgeRepository.from_session(session).get(
                    scope, ref.run_id
                )
                if intent is None or str(ref.run_id) != task.aggregate_id:
                    raise ResourceUnavailable("copied judge authority unavailable")
                collected.append(
                    {
                        "kind": "judge",
                        "run_id": str(ref.run_id),
                        "materials_digest": context_digest(intent["materials"]),
                    }
                )
            if ref.source_id.startswith("activity:"):
                _, identity, generation = ref.source_id.split(":")
                identities[(ref.run_id, UUID(identity), int(generation))] = ref
        if identities:
            rows = (
                (
                    await session.execute(
                        text("""
SELECT c.run_id,c.activity_id,c.generation,c.context_dependencies
 FROM execution_public_content c JOIN execution_content_bindings b USING(content_id)
 WHERE c.scope_key=:scope AND b.scope_key=:scope AND c.phase='output'
 AND c.activity_id=ANY(CAST(:activities AS uuid[]))
"""),
                        {
                            "scope": f"team:{scope.team_id}"
                            if scope.team_id
                            else f"user:{scope.user_id}",
                            "activities": list({key[1] for key in identities}),
                        },
                    )
                )
                .mappings()
                .all()
            )
            found = set()
            for row in rows:
                key = (row["run_id"], row["activity_id"], row["generation"])
                if key in identities:
                    found.add(key)
                    collected.extend(row["context_dependencies"])
            if found != set(identities):
                raise ResourceUnavailable("original copied context capture unavailable")
    unique = {context_digest(item): item for item in collected}
    dependencies = [unique[key] for key in sorted(unique)]
    try:
        await validate_captured_dependencies(session, scope, dependencies)
    except ResourceUnavailable as error:
        raise ContextCaptureUnavailable("copied context is no longer available") from error
    return dependencies
