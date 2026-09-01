"""Endpoint-level supplement for the anonymous A2A agent-card (G1 / A4).

``test_a2a_agent_card.py`` (A4) already pins the *service* contract: the card
builder opts into the global-only skill path and excludes private/team skills.
This file adds the missing HTTP-boundary assertions for
``GET /.well-known/agent-card.json``:

* it is reachable anonymously -- the route carries no auth dependency, so no
  bearer token or session cookie is required; and
* driven through the real ``A2AServerService`` over HTTP, an anonymous caller
  sees only ``visibility=global`` skills, never private or team ones.

Only ``well_known_router`` is mounted so the assertions do not depend on the
full application lifespan (which needs a seeded runtime-policy head in the DB).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.a2a_server_service import A2AServerService
from app.domain.models.skill import ResourceVisibility, Skill
from app.interfaces.endpoints.a2a_routes import well_known_router
from app.interfaces.service_dependencies import get_a2a_server_service


class _RecordingSkillService:
    """Honours the same visibility contract as the DB repo and records kwargs."""

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
        return list(skills)


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


@pytest.fixture
def agent_card_client():
    skill_service = _RecordingSkillService(_SKILLS)
    service = A2AServerService(
        agent_service=None,
        session_service=None,
        skill_service=skill_service,
        inference_model_service=None,
        policy_heads=None,
        breaker=None,
    )
    app = FastAPI()
    app.include_router(well_known_router)
    app.dependency_overrides[get_a2a_server_service] = lambda: service
    with TestClient(app) as client:
        yield client, skill_service


def test_agent_card_is_served_anonymously(agent_card_client):
    # No Authorization header, no cookie: a public discovery endpoint.
    client, _ = agent_card_client

    resp = client.get("/.well-known/agent-card.json")

    assert resp.status_code == 200
    assert "skills" in resp.json()


def test_agent_card_exposes_only_global_skills(agent_card_client):
    client, skill_service = agent_card_client

    resp = client.get("/.well-known/agent-card.json")

    assert resp.status_code == 200
    card = resp.json()
    names = {skill["name"] for skill in card["skills"]}
    descriptions = {skill["description"] for skill in card["skills"]}

    assert names == {"Global Coding"}
    assert "Tenant Secret" not in names
    assert "Team Playbook" not in names
    assert "private tenant-only skill" not in descriptions
    assert "team-scoped skill" not in descriptions
    # The card builder must have opted into the safe global-only path.
    assert skill_service.calls == [{"enabled_only": True, "scope": None, "global_only": True}]
