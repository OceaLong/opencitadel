"""Supplement fixed captures inside their existing consistent transaction."""

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.execution.query_observation import named_query
from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority


async def chart_facts(db, scope, principal, *, signing_secret, operation, capture_id):
    signed = await DBCurrentAuthority(db, signing_secret=signing_secret).signed(
        scope, principal, operation=operation, capture_id=capture_id
    )
    try:
        return await db.scalar(
            named_query(
                text("SELECT public.opencitadel_analysis_chart_facts(:body,:signature)"),
                "analysis.charts",
            ),
            signed,
        )
    except DBAPIError as error:
        reason = str(error.orig)
        if "analysis_authorization" in reason:
            raise PermissionError("analysis_authorization_revoked") from None
        if "analysis_refresh_required" in reason:
            raise ValueError("analysis_refresh_required") from None
        if "comparison_not_found" in reason:
            raise ValueError("comparison_not_found") from None
        raise
