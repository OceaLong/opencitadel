"""Shared current identity stamp, signed to the actual request principal and SQL context."""

import hashlib
import hmac
import json
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


class DBCurrentAuthority:
    def __init__(self, db, *, signing_secret):
        if not signing_secret:
            raise ValueError("authority_signing_secret_required")
        self.db, self.secret = db, signing_secret

    async def signed(self, scope, principal, **operation):
        if scope.user_id != principal.user_id:
            raise PermissionError("analysis_scope_mismatch")
        body = json.dumps(
            {
                **operation,
                "principal": principal.model_dump(mode="json"),
                "scope": "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id,
                "expires": time.time() + 30,
                "authorization_signature": await self.db.scalar(
                    text("SELECT current_setting('app.auth_signature',true)")
                ),
            },
            sort_keys=True,
            default=str,
            allow_nan=False,
            separators=(",", ":"),
        )
        signature = hmac.new(
            self.secret.encode(),
            ("opencitadel:analysis:authority:v1:" + body).encode(),
            hashlib.sha256,
        ).hexdigest()
        return {"body": body, "signature": signature}

    async def read_current(self, scope, principal, *, write=False):
        if write and principal.is_auditor:
            raise PermissionError("analysis_read_only_principal")
        try:
            revision = await self.db.scalar(
                text("SELECT public.opencitadel_analysis_authority(:body,:signature)"),
                await self.signed(scope, principal),
            )
        except DBAPIError as exc:
            if "analysis_authorization" in str(exc.orig):
                raise PermissionError("analysis_authorization_revoked") from None
            raise
        if revision is None:
            raise PermissionError("analysis_authorization_unavailable")
        return revision
