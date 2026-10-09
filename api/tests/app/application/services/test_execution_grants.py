from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole


def test_execution_grants_are_scoped_and_auditor_cannot_write():
    from app.application.services.capability_service import execution_grants

    auditor = WorkspaceContext(
        principal=Principal(user_id="a", global_role=GlobalRole.AUDITOR),
        scope=OwnerScope.personal("a"),
    )
    assert execution_grants(auditor) == ["execution.read", "evaluation.read"]
    user = WorkspaceContext(principal=Principal(user_id="a"), scope=OwnerScope.personal("a"))
    assert "evaluation.run" in execution_grants(user)
    assert "evaluation.environment.manage" not in execution_grants(user)
    foreign = user.model_copy(update={"scope": OwnerScope.team("a", "foreign")})
    assert execution_grants(foreign) == []
