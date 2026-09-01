"""Security regression: the anonymous A2A agent-card must expose only global Skills.

`GET /.well-known/agent-card.json` is an unauthenticated public discovery
endpoint. Before the fix, `build_agent_card` called `list_skills(enabled_only=True)`
with no scope, and `DBSkillRepository._apply_scope` returned every tenant's rows
when `scope is None`, leaking private/team Skill names and descriptions across
tenants. These tests pin the fix: the card must only ever contain global Skills.
"""

import pytest

from app.application.services.a2a_server_service import A2AServerService
from app.domain.models.skill import ResourceVisibility, Skill


class _RecordingSkillService:
    """Skill service that honours the same visibility contract as the DB repo.

    It records the exact kwargs the card builder used so we can assert the
    call site opts into the safe global-only path, and it filters its stored
    skills the way `_apply_scope(global_only=True)` does.
    """

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = skills
        self.calls: list[dict] = []

    async def list_skills(
        self,
        enabled_only: bool = False,
        scope=None,
        *,
        global_only: bool = False,
    ) -> list[Skill]:
        self.calls.append(
            {"enabled_only": enabled_only, "scope": scope, "global_only": global_only}
        )
        skills = self._skills
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        if global_only:
            skills = [s for s in skills if s.visibility == ResourceVisibility.GLOBAL.value]
        elif scope is None:
            # Mirrors the (dangerous) unscoped repo path: returns everything.
            return list(skills)
        return list(skills)


def _make_service(skills: list[Skill]) -> tuple[A2AServerService, _RecordingSkillService]:
    skill_service = _RecordingSkillService(skills)
    service = A2AServerService(
        agent_service=None,
        session_service=None,
        skill_service=skill_service,
        inference_model_service=None,
        policy_heads=None,
        breaker=None,
    )
    return service, skill_service


_SKILLS = [
    Skill(
        id="s-global",
        name="Global Coding",
        slug="global-coding",
        description="public global skill",
        visibility=ResourceVisibility.GLOBAL.value,
    ),
    Skill(
        id="s-private",
        name="Tenant Secret",
        slug="tenant-secret",
        description="private tenant-only skill",
        visibility=ResourceVisibility.PRIVATE.value,
        owner_user_id="other-tenant",
    ),
    Skill(
        id="s-team",
        name="Team Playbook",
        slug="team-playbook",
        description="team-scoped skill",
        visibility=ResourceVisibility.PRIVATE.value,
        team_id="team-9",
    ),
]


@pytest.mark.asyncio
async def test_agent_card_requests_global_only_enabled_skills():
    service, skill_service = _make_service(_SKILLS)

    await service.build_agent_card("https://example.com")

    assert skill_service.calls == [{"enabled_only": True, "scope": None, "global_only": True}]


@pytest.mark.asyncio
async def test_agent_card_excludes_private_and_team_skills():
    service, _ = _make_service(_SKILLS)

    card = await service.build_agent_card("https://example.com")

    names = {skill["name"] for skill in card["skills"]}
    descriptions = {skill["description"] for skill in card["skills"]}

    assert names == {"Global Coding"}
    assert "Tenant Secret" not in names
    assert "Team Playbook" not in names
    assert "private tenant-only skill" not in descriptions
    assert "team-scoped skill" not in descriptions
