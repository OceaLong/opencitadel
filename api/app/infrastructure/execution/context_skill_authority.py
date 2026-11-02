"""Freeze selected skill instructions and recheck their current authority at every use."""

from sqlalchemy import or_, select

from app.domain.execution.context_resources import ContextSkillEvidence, skill_content_digest
from app.domain.services.skills.skill_loader import render_active
from app.infrastructure.models.skill import SkillORM


async def _current(session, scope, skill_id):
    owner = (
        SkillORM.team_id == scope.team_id
        if scope.team_id
        else (SkillORM.owner_user_id == scope.user_id) & SkillORM.team_id.is_(None)
    )
    row = await session.scalar(
        select(SkillORM)
        .where(SkillORM.id == skill_id, or_(SkillORM.visibility == "global", owner))
        .with_for_update(read=True)
    )
    if row is None or not row.enabled:
        raise ValueError("CONTEXT_RESOURCE_AUTHORIZATION_REVOKED")
    return row.to_domain()


async def freeze_context_skill(*, work, scope, payload, run_id=None):
    result = {key: value for key, value in payload.items() if key != "_context_skill"}
    if not result.get("skill_id"):
        return result
    if work is None:
        raise ValueError("CONTEXT_ADMISSION_TRANSACTION_REQUIRED")
    skill = await _current(work.db_session, scope, result["skill_id"])
    result["_context_skill"] = ContextSkillEvidence(
        skill_id=skill.id, content_digest=skill_content_digest(skill), rendered=render_active(skill)
    ).model_dump(mode="json")
    return result


async def validate_context_skill(session, scope, payload):
    if not payload.get("skill_id"):
        if payload.get("_context_skill") is not None:
            raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
        return None
    evidence = ContextSkillEvidence.model_validate(payload.get("_context_skill"))
    if evidence.skill_id != payload["skill_id"]:
        raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    skill = await validate_skill_version(session, scope, evidence.skill_id, evidence.content_digest)
    if render_active(skill) != evidence.rendered:
        raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
    return evidence


async def validate_skill_version(session, scope, skill_id, digest):
    skill = await _current(session, scope, skill_id)
    if skill_content_digest(skill) != digest:
        raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
    return skill
