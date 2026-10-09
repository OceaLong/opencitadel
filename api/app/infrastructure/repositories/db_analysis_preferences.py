"""Scoped optional timezone with current authority and transactional command receipts."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority
from app.infrastructure.repositories.db_execution_analysis_repository import (
    DBExecutionAnalysisRepository,
)


class DBAnalysisPreferences:
    def __init__(self, session_factory, *, signing_secret):
        self.transactions = DBExecutionAnalysisRepository(
            session_factory, signing_secret=signing_secret
        )
        self.secret = signing_secret

    async def _operation(self, scope, principal, operation, **payload):
        async with self.transactions.transaction(scope, principal, current=True) as db:
            signed = await DBCurrentAuthority(db, signing_secret=self.secret).signed(
                scope, principal, operation=operation, **payload
            )
            try:
                result = await db.scalar(
                    text("SELECT public.opencitadel_analysis_preference(:body,:signature)"), signed
                )
                if operation == "update":
                    await db.commit()
                return result
            except DBAPIError as error:
                reason = str(error.orig)
                if "analysis_authorization" in reason or "preference_permission_denied" in reason:
                    raise PermissionError("analysis_preference_permission_denied") from None
                if "preference_conflict" in reason or "preference_request_conflict" in reason:
                    raise ValueError("analysis_preference_conflict") from None
                if "invalid_analysis_preference" in reason:
                    raise ValueError("invalid_analysis_preference") from None
                raise

    async def get(self, scope, principal):
        return await self._operation(scope, principal, "get")

    async def update(self, scope, principal, *, request_id, expected_revision, timezone):
        if principal.is_auditor or (
            scope.team_id and principal.team_roles.get(scope.team_id) not in {"owner", "admin"}
        ):
            raise PermissionError("analysis_preference_permission_denied")
        if (
            not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 128
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            raise ValueError("invalid_analysis_preference")
        if timezone is not None:
            try:
                ZoneInfo(timezone)
            except (TypeError, ValueError, ZoneInfoNotFoundError):
                raise ValueError("invalid_analysis_preference") from None
        return await self._operation(
            scope,
            principal,
            "update",
            request_id=request_id,
            expected_revision=expected_revision,
            timezone=timezone,
        )
