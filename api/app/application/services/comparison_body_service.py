"""Safe fixed-comparison body pages reuse the execution UTF-8 and sanitization contract."""

import hashlib
import json

from app.application.execution.content_sanitization import PUBLIC_CONTENT_POLICY, sanitize_content
from app.application.services.execution_content_service import (
    ContentPage,
    ExecutionContentService,
    _limit_bytes,
)


class ComparisonBodyService:
    def __init__(self, repository, artifacts, *, secret):
        self.repository, self.artifacts = repository, artifacts
        self.pages = ExecutionContentService(None, None, None, cursor_secret=secret.encode())

    async def read(
        self,
        scope,
        principal,
        comparison_id,
        revision,
        run_id,
        step_id,
        kind,
        *,
        artifact_id=None,
        version=None,
        cursor=None,
        limit_bytes=65536,
    ):
        _limit_bytes(limit_bytes)
        if scope.user_id != principal.user_id or kind not in {"input", "output", "artifact"}:
            raise PermissionError("comparison_body_denied")
        identity = {
            "caller": principal.user_id,
            "comparison": comparison_id,
            "revision": revision,
            "run": run_id,
            "step": step_id,
            "kind": kind,
            "artifact": artifact_id,
            "version": version,
            "policy": PUBLIC_CONTENT_POLICY,
        }
        state = self.pages._state(scope, identity, cursor)
        before = await self.repository.current(scope, principal, comparison_id, revision)
        fields = {}
        if kind == "artifact":
            if not artifact_id or type(version) is not int or version < 1:
                raise ValueError("invalid_comparison_artifact")
            value = await self.artifacts.read(
                scope,
                principal,
                comparison_id,
                revision,
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "artifact_id": artifact_id,
                    "version": version,
                },
                limit=2097152,
            )
            if value.truncated:
                result = ContentPage(availability="unavailable", reason="retained_preview_limit")
                if (
                    await self.repository.current(scope, principal, comparison_id, revision)
                    != before
                ):
                    raise PermissionError("comparison_body_authority_changed")
                return result
            try:
                original = value.data.decode("utf-8")
            except UnicodeError:
                original = None
            if original is None:
                result = ContentPage(availability="unavailable", reason="binary_metadata_only")
                if (
                    await self.repository.current(scope, principal, comparison_id, revision)
                    != before
                ):
                    raise PermissionError("comparison_body_authority_changed")
                return result
            safe = sanitize_content(original)
            if value.kind == "web":
                from app.application.services.artifact_service import sanitize_html_for_preview

                safe = sanitize_html_for_preview(safe)
            data = safe.encode()
            fields = {
                "artifact_id": artifact_id,
                "version": version,
                "content_type": "text/html" if value.kind == "web" else "text/markdown",
                "redacted": safe != original,
            }
        else:
            row = await self.repository.content_snapshot(
                scope, principal, comparison_id, revision, run_id, step_id, kind
            )
            if row is None:
                if (
                    await self.repository.current(scope, principal, comparison_id, revision)
                    != before
                ):
                    raise PermissionError("comparison_body_authority_changed")
                return ContentPage(availability="unavailable", reason="retained_data_unavailable")
            if row["redacted"]:
                if (
                    await self.repository.current(scope, principal, comparison_id, revision)
                    != before
                ):
                    raise PermissionError("comparison_body_authority_changed")
                return ContentPage(availability="unavailable", reason="retained_data_unavailable")
            if hashlib.sha256(row["body"].encode()).hexdigest() != row["content_digest"]:
                raise ValueError("comparison_content_unavailable")
            decoded = json.loads(row["body"])
            safe = sanitize_content(decoded)
            data = json.dumps(
                safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            fields = {
                "content_id": str(row["content_id"]),
                "content_type": "application/json",
                "redacted": row["redacted"] or safe != decoded,
            }
        if await self.repository.current(scope, principal, comparison_id, revision) != before:
            raise PermissionError("comparison_body_authority_changed")
        return self.pages._page(scope, identity, data, state, limit_bytes, **fields)
