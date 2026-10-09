"""Run authorization and generation fencing around the existing public reader."""

import hashlib

from app.application.ports.execution_view import ViewRevisionExpired
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
from app.domain.models.scope import Principal
from app.domain.models.user import UserStatus
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
from app.infrastructure.execution.postgres_public_projection import PostgresPublicProjection
from app.infrastructure.repositories.db_team_repository import DBTeamRepository
from app.infrastructure.repositories.db_user_repository import DBUserRepository


class PostgresRunPublicEvents:
    def __init__(self, *, session_factory, authorization, cursor):
        self.session_factory = session_factory
        self.authorization = authorization
        self.cursor = cursor
        self.views = PostgresExecutionView(
            session_factory=session_factory, authorization=authorization
        )
        self.reader = PostgresPublicProjection(
            session_factory=session_factory, authorization=authorization, cursor=cursor
        )

    async def revalidate(self, scope):
        """Reload live security metadata, then sign only the refreshed authority.

        The system transaction reads identity metadata only, matching auth-context
        middleware. All execution data remains under the refreshed user scope.
        """
        original = self.authorization
        if original.mode == AuthorizationMode.SYSTEM:
            return
        principal = original.principal
        if principal is None or original.scope != scope:
            raise PermissionError("execution workspace authority revoked")
        identity = PostgresExecutionView(
            session_factory=self.session_factory,
            authorization=AuthorizationContext.system("execution-stream-auth"),
        )
        async with identity.transaction() as session:
            user = await DBUserRepository(session).get_by_id(principal.user_id)
            if (
                not user
                or user.status != UserStatus.ACTIVE
                or user.token_version != principal.token_version
            ):
                raise PermissionError("execution principal revoked")
            roles = {}
            if scope.team_id:
                teams = DBTeamRepository(session)
                team = await teams.get_by_id(scope.team_id)
                member = await teams.get_member(scope.team_id, principal.user_id)
                if not team or not member:
                    raise PermissionError("execution workspace membership revoked")
                roles[scope.team_id] = member.role
            current = AuthorizationContext.for_principal(
                Principal(
                    user_id=user.id,
                    global_role=user.global_role,
                    token_version=user.token_version,
                    team_roles=roles,
                ),
                scope=scope,
                request_id=original.request_id,
            )
        self.views = PostgresExecutionView(
            session_factory=self.session_factory, authorization=current
        )
        self.reader = PostgresPublicProjection(
            session_factory=self.session_factory, authorization=current, cursor=self.cursor
        )

    async def _generation(self, scope, run_id):
        async with self.views.transaction() as session:
            await self.views.capture_run(session, scope, run_id)
            active = await self.views.active_generation(session, scope)
        # The legacy public feed can be rebuilt independently of F04 shadows.
        # Its retained origin identifies that stream incarnation; normal appends
        # leave it unchanged. Never equate the moving run revision with an epoch.
        first = await self.reader.list_events(owner_scope=scope, run_id=run_id, limit=1)
        origin = first.events[0] if first.events else None
        return hashlib.sha256(
            f"{active}:{origin.event_id if origin else ''}:{origin.cursor if origin else ''}".encode()
        ).hexdigest()

    async def read(self, scope, run_id, *, after, before, latest, limit, generation):
        await self.revalidate(scope)
        active = await self._generation(scope, run_id)
        if generation is not None and active != generation:
            raise ViewRevisionExpired("event generation retired")
        page = await self.reader.list_events(
            owner_scope=scope, run_id=run_id, after=after, before=before, latest=latest, limit=limit
        )
        # A rebuild/retention boundary or authorization change between reader
        # transactions must not produce cursors tied to the retired origin.
        if await self._generation(scope, run_id) != active:
            raise ViewRevisionExpired("event generation changed during read")
        return active, page
