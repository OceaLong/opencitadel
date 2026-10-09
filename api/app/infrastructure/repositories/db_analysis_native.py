"""Bounded native page adapters; opaque cursors bind caller, scope and fixed capture."""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.infrastructure.execution.query_observation import named_query
from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority


async def native_scalar(db, statement, signed):
    try:
        query = text(statement)
        if statement == "SELECT public.opencitadel_analysis_native_page(:body,:signature)":
            query = named_query(query, "analysis.page")
        return await db.scalar(query, signed)
    except DBAPIError as error:
        reason = str(error.orig)
        if "authorization" in reason:
            raise PermissionError("analysis_authorization_revoked") from None
        for code in (
            "analysis_refresh_required",
            "analysis_query_invalid",
            "comparison_not_found",
            "comparison_member_unavailable",
            "comparison_detail_unavailable",
            "invalid_comparison_request",
        ):
            if code in reason:
                raise ValueError(code) from None
        raise


def cursor_cipher(secret):
    return Fernet(
        base64.urlsafe_b64encode(hashlib.sha256(("analysis-page-v1:" + secret).encode()).digest())
    )


def page_after(secret, scope, principal, watermark, cursor):
    if cursor is None:
        return -1
    try:
        if not isinstance(cursor, str) or len(cursor) > 4096:
            raise ValueError
        body = json.loads(cursor_cipher(secret).decrypt(cursor.encode()))
        if body["owner"] != [scope.model_dump(mode="json"), principal.user_id, watermark]:
            raise ValueError
        after = body["after"]
        if type(after) is not int or not 0 <= after < 100000:
            raise ValueError
        return after
    except (ValueError, KeyError, InvalidToken, UnicodeError):
        raise ValueError("invalid_analysis_cursor") from None


async def run_page(repository, scope, principal, capture, *, cursor=None, limit=50):
    after = page_after(repository.secret, scope, principal, capture.watermark, cursor)
    async with repository.transaction(scope, principal) as db:
        signed = await DBCurrentAuthority(db, signing_secret=repository.secret).signed(
            scope,
            principal,
            operation="read",
            capture_id=capture.watermark,
            after=after,
            limit=limit,
        )
        body = await native_scalar(
            db, "SELECT public.opencitadel_analysis_native_page(:body,:signature)", signed
        )
    rows = body["items"]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = (
            cursor_cipher(repository.secret)
            .encrypt(
                json.dumps(
                    {
                        "owner": [
                            scope.model_dump(mode="json"),
                            principal.user_id,
                            capture.watermark,
                        ],
                        "after": rows[limit - 1]["ordinal"],
                    },
                    sort_keys=True,
                ).encode()
            )
            .decode()
        )
    return {
        "availability": body["availability"],
        "watermark": capture.watermark,
        "items": [{k: v for k, v in row.items() if k != "ordinal"} for row in rows[:limit]],
        "next_cursor": next_cursor,
    }


async def comparison_context(db, scope, principal, *, secret, capture_id, run_ids):
    signed = await DBCurrentAuthority(db, signing_secret=secret).signed(
        scope, principal, operation="native_context", capture_id=capture_id, run_ids=run_ids
    )
    return await native_scalar(
        db, "SELECT public.opencitadel_comparison_native_context(:body,:signature)", signed
    )
