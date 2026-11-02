"""Deterministic platform instructions from admitted, independently validated skill evidence."""

from app.application.execution.system_prompt import platform_system_prompt
from app.domain.execution.context_resources import ContextSkillEvidence


def context_system_prompt(payload, *, family):
    prompt = platform_system_prompt(family)
    if payload.get("skill_id"):
        evidence = ContextSkillEvidence.model_validate(payload.get("_context_skill"))
        if evidence.skill_id != payload["skill_id"]:
            raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
        prompt += "\n\n" + evidence.rendered
    return prompt
