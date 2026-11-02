"""Bounded formal source queries with signed cursors tied to the accepted cut."""

import hashlib
import hmac
import re

from sqlalchemy import select, text

from app.application.ports.context_sources import ContextSourcePage
from app.domain.execution.context_models import (
    AcceptedContextCut,
    FormalContextSource,
    context_digest,
)
from app.infrastructure.execution.models import ExecutionPublicEventORM


class PostgresContextSources:
    def __init__(self, session, *, signing_secret: str):
        if not signing_secret:
            raise ValueError("context cursor signing authority missing")
        self.session = session
        self.secret = signing_secret.encode()

    async def legacy_conversation(self, scope, collection_id):
        # Borrow the admission/session-lock transaction. Opening a second pool
        # checkout here can deadlock a single-connection API or evaluation UoW.
        table = ExecutionPublicEventORM
        scope_filter = (
            table.team_id == scope.team_id
            if scope.team_id
            else (table.owner_user_id == scope.user_id) & table.team_id.is_(None)
        )
        rows = (
            await self.session.execute(
                select(table.event_type, table.payload)
                .where(
                    scope_filter,
                    table.source_entity_type == "session",
                    table.source_entity_id == collection_id,
                )
                .order_by(table.seq.desc())
                .limit(100)
            )
        ).all()
        return [
            {"role": row.payload["role"], "content": row.payload["message"]}
            for row in reversed(rows)
            if row.event_type == "message"
            and row.payload.get("role") in {"user", "assistant"}
            and isinstance(row.payload.get("message"), str)
        ]

    async def freeze(self, scope, collection_id: str, *, request_id=None) -> AcceptedContextCut:
        scope_key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
        body = await self.session.scalar(
            text("SELECT public.opencitadel_context_cut(:scope,:collection,:request)"),
            {"scope": scope_key, "collection": collection_id, "request": request_id},
        )
        return AcceptedContextCut.model_validate(body)

    def _cursor(self, cut, position):
        material = (
            f"context-source-page:v1:{context_digest(cut.model_dump(mode='json'))}:{position}"
        )
        signature = hmac.new(self.secret, material.encode(), hashlib.sha256).hexdigest()
        return f"{position}.{signature}"

    def _position(self, cut, cursor):
        if (
            not isinstance(cursor, str)
            or re.fullmatch(r"[0-9]{1,20}\.[0-9a-f]{64}", cursor) is None
        ):
            raise ValueError("context source cursor invalid")
        try:
            position = int(cursor.split(".", 1)[0])
        except (ValueError, AttributeError) as error:
            raise ValueError("context source cursor invalid") from error
        if (
            position < 0
            or position > cut.through_position
            or not hmac.compare_digest(cursor, self._cursor(cut, position))
        ):
            raise ValueError("context source cursor does not match boundary")
        return position

    async def page(self, cut, *, cursor=None, limit=200):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("context source page limit outside bounds")
        position = self._position(cut, cursor) if cursor is not None else 0
        rows = await self.session.scalar(
            text(
                "SELECT public.opencitadel_context_source_page(:scope,:collection,:through,:after,:limit)"
            ),
            {
                "scope": cut.scope_key,
                "collection": cut.collection_id,
                "through": cut.through_position,
                "after": position,
                "limit": limit + 1,
            },
        )
        values = tuple(FormalContextSource.model_validate(row) for row in rows)
        for value in values:
            prefix = "execution/inputs/" if value.kind == "user" else "execution/results/"
            if not value.object_ref.startswith(prefix) or not value.object_ref.endswith(
                f"/{value.object_digest}.json"
            ):
                raise ValueError("formal private context source reference unavailable")
        return ContextSourcePage(
            sources=values[:limit],
            next_cursor=self._cursor(cut, values[limit - 1].formal_position)
            if len(values) > limit
            else None,
        )
