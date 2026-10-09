"""Opaque, signed and context-bound execution-view cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from uuid import UUID

from pydantic import ValidationError

from app.domain.models.playback import PlaybackBoundary


class InvalidViewCursor(ValueError):
    code = "invalid_view_cursor"
    recoverable = True


class ViewCursor:
    def __init__(
        self, *, secret: bytes, previous_secrets: tuple[bytes, ...] = (), envelope_version=1
    ):
        if len(secret) < 16 or any(len(value) < 16 for value in previous_secrets):
            raise ValueError("cursor secret must be at least 16 bytes")
        self._secret = secret
        self._decode_secrets = (secret, *previous_secrets)
        self._envelope_version = envelope_version

    def encode(self, boundary: PlaybackBoundary, scope_key: str, query_digest: str) -> str:
        if self._envelope_version != 1 or not scope_key or not query_digest:
            raise ValueError("unsupported or incomplete view cursor context")
        document = {
            "v": 1,
            "scope": scope_key,
            "query": query_digest,
            "boundary": boundary.model_dump(mode="json"),
        }
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def decode(
        self,
        cursor: str,
        *,
        expected_run_id: UUID,
        expected_scope_key: str,
        expected_query_digest: str,
        expected_projector_version: int,
        expected_projection_revision: int | None = None,
        expected_observed_order: int | None = None,
    ) -> PlaybackBoundary:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            if len(raw) <= 32:
                raise ValueError
            payload, signature = raw[:-32], raw[-32:]
            if not any(
                hmac.compare_digest(signature, hmac.new(key, payload, hashlib.sha256).digest())
                for key in self._decode_secrets
            ):
                raise ValueError
            document = json.loads(payload)
            if document.get("v") != self._envelope_version or self._envelope_version != 1:
                raise ValueError
            boundary = PlaybackBoundary.model_validate(document["boundary"])
            expected = (
                boundary.run_id == expected_run_id
                and document.get("scope") == expected_scope_key
                and document.get("query") == expected_query_digest
                and boundary.projector_version == expected_projector_version
            )
            if (expected_projection_revision is None) != (expected_observed_order is None):
                raise ValueError
            if expected_projection_revision is not None:
                expected = (
                    expected
                    and boundary.projection_revision == expected_projection_revision
                    and boundary.observed_order == expected_observed_order
                )
            if not expected:
                raise ValueError
            return boundary
        except (
            AttributeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise InvalidViewCursor("view cursor is invalid or stale") from error


__all__ = ["InvalidViewCursor", "ViewCursor"]
