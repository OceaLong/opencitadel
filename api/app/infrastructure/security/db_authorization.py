import hashlib
import hmac

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.request_context import get_request_id
from app.application.security.authorization_context import get_authorization_context
from app.domain.models.authorization import AuthorizationContext

_AUTHORIZATION_SQL = text(
    """
    SELECT
        set_config('app.auth_mode', :auth_mode, true),
        set_config('app.user_id', :user_id, true),
        set_config('app.team_id', :team_id, true),
        set_config('app.is_admin', :is_admin, true),
        set_config('app.request_id', :request_id, true),
        set_config('app.system_actor', :system_actor, true),
        set_config('app.is_auditor', :is_auditor, true),
        set_config('app.auth_signature', :auth_signature, true)
    """
)

_CLAIM_SEPARATOR = "\x1f"


def _sign_authorization_claims(
    claims: dict[str, str],
    *,
    signing_secret: str,
) -> str:
    if not signing_secret:
        raise ValueError("database authorization signing secret must not be empty")
    payload = _CLAIM_SEPARATOR.join(
        claims[key]
        for key in (
            "auth_mode",
            "user_id",
            "team_id",
            "is_admin",
            "request_id",
            "system_actor",
            "is_auditor",
        )
    )
    return hmac.new(
        signing_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def _signed_claims(
    *,
    auth_mode: str,
    user_id: str,
    team_id: str,
    is_admin: str,
    request_id: str,
    system_actor: str,
    is_auditor: str,
    signing_secret: str,
) -> dict[str, str]:
    claims = {
        "auth_mode": auth_mode,
        "user_id": user_id,
        "team_id": team_id,
        "is_admin": is_admin,
        "request_id": request_id,
        "system_actor": system_actor,
        "is_auditor": is_auditor,
    }
    return {
        **claims,
        "auth_signature": _sign_authorization_claims(
            claims,
            signing_secret=signing_secret,
        ),
    }


def configure_sync_system_authorization(
    connection: Connection,
    *,
    actor: str,
    signing_secret: str,
) -> None:
    """Authorize schema/data migration SQL inside its current transaction."""
    system_actor = actor.strip()
    if not system_actor:
        raise ValueError("system migration actor must not be empty")
    connection.execute(
        _AUTHORIZATION_SQL,
        _signed_claims(
            auth_mode="system",
            user_id="",
            team_id="",
            is_admin="false",
            request_id="",
            system_actor=system_actor,
            is_auditor="false",
            signing_secret=signing_secret,
        ),
    )


def configure_sync_authorization(
    connection: Connection,
    context: AuthorizationContext,
    *,
    signing_secret: str,
) -> AuthorizationContext:
    """Bind signed request authorization to a synchronous transaction."""

    request_id = context.request_id or get_request_id() or ""
    connection.execute(
        _AUTHORIZATION_SQL,
        _signed_claims(
            auth_mode=context.mode.value,
            user_id=context.user_id or "",
            team_id=context.team_id or "",
            is_admin="true" if context.is_admin else "false",
            request_id=request_id,
            system_actor=context.system_actor,
            is_auditor="true" if context.is_auditor else "false",
            signing_secret=signing_secret,
        ),
    )
    return context


async def configure_session_authorization(
    session: AsyncSession,
    context: AuthorizationContext | None = None,
    *,
    signing_secret: str | None = None,
) -> AuthorizationContext:
    """Bind an immutable authorization context to the current DB transaction."""
    resolved = context or get_authorization_context()
    request_id = resolved.request_id or get_request_id() or ""
    await session.execute(
        _AUTHORIZATION_SQL,
        _signed_claims(
            auth_mode=resolved.mode.value,
            user_id=resolved.user_id or "",
            team_id=resolved.team_id or "",
            is_admin="true" if resolved.is_admin else "false",
            request_id=request_id,
            system_actor=resolved.system_actor,
            is_auditor="true" if resolved.is_auditor else "false",
            signing_secret=(
                signing_secret
                or str(session.info.get("database_authorization_signing_secret") or "")
            ),
        ),
    )
    return resolved
