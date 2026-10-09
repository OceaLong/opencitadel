"""Opaque target-bound recovery over the retained public event stream."""

import base64
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from app.application.execution.public_projection import PublicEventPage
from app.application.ports.execution_view import ViewCursorInvalid
from app.domain.models.scope import OwnerScope


class RunPublicEventPort(Protocol):
    async def read(
        self,
        scope: OwnerScope,
        run_id: UUID,
        *,
        after: str | None,
        before: str | None,
        latest: bool,
        limit: int,
        generation: str | None,
    ) -> tuple[str, PublicEventPage]: ...


class ExecutionEventService:
    def __init__(
        self,
        port: RunPublicEventPort,
        *,
        cursor_secret: bytes,
        revalidate: Callable[[OwnerScope], Awaitable[None]] | None = None,
    ):
        if len(cursor_secret) < 16:
            raise ValueError("cursor secret too short")
        self.port, self.secret = port, cursor_secret
        self._revalidate = revalidate

    async def revalidate(self, scope: OwnerScope) -> None:
        if self._revalidate is not None:
            await self._revalidate(scope)

    def _encode(self, scope, run, generation, position, direction):
        raw = json.dumps(
            {
                "v": 1,
                "scope": f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
                "run": str(run),
                "generation": generation,
                "position": position,
                "direction": direction,
            },
            sort_keys=True,
        ).encode()
        return (
            base64.urlsafe_b64encode(raw + hmac.digest(self.secret, raw, "sha256"))
            .decode()
            .rstrip("=")
        )

    def _decode(self, scope, run, cursor, direction):
        try:
            if len(cursor) > 16384:
                raise ValueError
            value = base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
            raw, signature = value[:-32], value[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, raw, "sha256")):
                raise ValueError
            data = json.loads(raw)
            expected = {
                "v": 1,
                "scope": f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
                "run": str(run),
                "direction": direction,
            }
            if any(data.get(k) != v for k, v in expected.items()):
                raise ValueError
            return data["generation"], data["position"]
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            raise ViewCursorInvalid("event cursor does not match query") from error

    async def list_events(
        self,
        scope: OwnerScope,
        run_id: UUID,
        *,
        after: str | None = None,
        before: str | None = None,
        latest: bool = False,
        limit: int = 200,
    ) -> PublicEventPage:
        if (
            type(limit) is not int
            or not 1 <= limit <= 500
            or sum((after is not None, before is not None, latest)) > 1
        ):
            raise ViewCursorInvalid("invalid event page query")
        generation = None
        position = None
        if after is not None or before is not None:
            generation, position = self._decode(
                scope,
                run_id,
                after if after is not None else before,
                "after" if after is not None else "before",
            )
        generation, page = await self.port.read(
            scope,
            run_id,
            after=position if after is not None else None,
            before=position if before is not None else None,
            latest=latest,
            limit=limit,
            generation=generation,
        )

        def encode(position, direction="after"):
            return (
                self._encode(scope, run_id, generation, position, direction) if position else None
            )

        events = tuple(
            event.model_copy(
                update={
                    "cursor": (cursor := encode(event.cursor)),
                    "payload": {**event.payload, "event_id": cursor},
                }
            )
            for event in page.events
        )
        return PublicEventPage(
            events=events,
            next_cursor=encode(page.next_cursor),
            prev_cursor=encode(page.prev_cursor, "before"),
            has_earlier=page.has_earlier,
        )
