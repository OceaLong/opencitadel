"""Lock the consuming Run's original principal in the caller's transaction."""

from sqlalchemy import text

from app.domain.execution.context_models import context_digest
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.repositories.db_evaluation_budget_control_repository import (
    DBEvaluationBudgetControlRepository,
)
from app.infrastructure.repositories.db_evaluation_dataset_repository import (
    authorize_original_team,
    authorize_original_user,
)
from app.infrastructure.repositories.db_physical_requester_repository import (
    DBPhysicalRequesterRepository,
)


class ContextRunAuthority:
    def __init__(self, session, *, signing_secret):
        self.session = session
        self.secret = signing_secret

    async def lock(self, boundary):
        if boundary.current_run_id is None:
            raise ValueError("CONTEXT_REQUESTER_UNAVAILABLE")
        params = {"scope": boundary.scope_key, "run": boundary.current_run_id}
        owner = (
            (
                await self.session.execute(
                    text("""
SELECT owner_user_id,team_id FROM execution_stream_owners
 WHERE owner_scope_key=:scope AND stream_type='run'
 AND stream_id=CAST(CAST(:run AS uuid) AS text)
"""),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if owner is None:
            raise ValueError("CONTEXT_REQUESTER_UNAVAILABLE")
        scope = (
            OwnerScope.team(owner["owner_user_id"] or "", owner["team_id"])
            if owner["team_id"]
            else OwnerScope.personal(owner["owner_user_id"])
        )
        binding = await DBEvaluationBudgetControlRepository(self.session).binding(
            scope, boundary.current_run_id
        )
        if binding is not None:
            principal = Principal.model_validate(binding.requester)
        else:
            admitted = (
                (
                    await self.session.execute(
                        text("""
SELECT body FROM execution_configurations WHERE scope_key=:scope AND run_id=:run
 AND body->>'stage'='admission' ORDER BY created_at,id LIMIT 1
"""),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if admitted is None or "physical_requester" not in admitted["body"]:
                raise ValueError("CONTEXT_REQUESTER_UNAVAILABLE")
            proof = DBPhysicalRequesterRepository(self.session, signing_secret=self.secret).verify(
                admitted["body"]["physical_requester"], scope=scope, run_id=boundary.current_run_id
            )
            # A system actor is not permission to read a user's private history.
            if proof.get("kind") != "user":
                raise ValueError("CONTEXT_REQUESTER_UNAVAILABLE")
            principal = Principal.model_validate(proof["principal"])
        if scope.team_id:
            scope = OwnerScope.team(principal.user_id, scope.team_id)
        user = (
            (
                await self.session.execute(
                    text(
                        "SELECT status,token_version,global_role FROM users WHERE id=:id FOR SHARE"
                    ),
                    {"id": principal.user_id},
                )
            )
            .mappings()
            .first()
        )
        try:
            authorize_original_user(scope, principal, write=False, user=user)
            if scope.team_id:
                role = await self.session.scalar(
                    text(
                        "SELECT role FROM team_members WHERE team_id=:team AND user_id=:user FOR SHARE"
                    ),
                    {"team": scope.team_id, "user": principal.user_id},
                )
                authorize_original_team(scope, principal, role)
        except PermissionError as error:
            raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED") from error
        return scope, context_digest(principal.model_dump(mode="json"))
