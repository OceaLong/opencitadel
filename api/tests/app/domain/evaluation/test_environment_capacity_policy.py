import pytest

from tests.app.infrastructure.repositories.test_environment_capacity import lease_for


def test_verified_clean_cannot_become_occupied_again():
    from app.domain.evaluation.environment import transition
    from app.domain.models.scope import OwnerScope, Principal

    scope = OwnerScope.personal("user")
    clean = lease_for(scope, Principal(user_id="user")).model_copy(
        update={"state": "verified_clean"}
    )
    with pytest.raises(ValueError, match="invalid_environment_transition"):
        transition(clean, "quarantine")
