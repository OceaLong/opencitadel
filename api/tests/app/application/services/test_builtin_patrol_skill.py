from app.application.services.skill_service import BUILTIN_SKILLS


def test_deterministic_patrol_families_are_not_exposed_as_llm_skills():
    slugs = {item.slug for item in BUILTIN_SKILLS}
    assert "ops-patrol" not in slugs
    assert "ops-patrol-remediation" not in slugs
