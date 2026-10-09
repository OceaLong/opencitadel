"""Signed narrow archive command and current admission guard."""

import hashlib
import hmac
import json
import time

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
from app.infrastructure.repositories.db_evaluation_dataset_repository import params


class DBEvaluationArchiveRepository:
    def __init__(self, work, *, signing_secret):
        self.work, self.db, self.secret = work, work.db_session, signing_secret

    async def require_active(self, scope, kind, identity):
        if await self.db.scalar(
            text(
                "SELECT 1 FROM evaluation_resource_archives WHERE scope_key=:scope AND kind=:kind AND resource_id=:id"
            ),
            params(scope, kind=kind, id=identity),
        ):
            raise ValueError("evaluation_resource_archived")

    async def archive(self, scope, principal, **command):
        command["identity"] = str(command["identity"])
        payload = {
            **command,
            "scope": params(scope)["scope"],
            "principal": principal.model_dump(mode="json"),
            "expires": time.time() + 30,
            "authorization_signature": await self.db.scalar(
                text("SELECT current_setting('app.auth_signature',true)")
            ),
        }
        payload["fingerprint"] = hashlib.sha256(
            json.dumps(command, sort_keys=True).encode()
        ).hexdigest()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            self.secret.encode(), ("opencitadel:e12:archive:v1:" + encoded).encode(), hashlib.sha256
        ).hexdigest()
        try:
            return await self.db.scalar(
                text("SELECT public.opencitadel_e12_archive(:encoded,:signature)"),
                {"encoded": encoded, "signature": signature},
            )
        except DBAPIError as error:
            message = str(error.orig)
            if "archive_conflict" in message:
                raise DatasetConflict("archive_conflict") from error
            if "archive_not_found" in message:
                raise DatasetNotFound("archive_not_found") from error
            if "archive_authorization" in message:
                raise PermissionError("archive_authorization_denied") from error
            if "archive_" in message:
                raise ValueError("archive_resource_busy") from error
            raise
