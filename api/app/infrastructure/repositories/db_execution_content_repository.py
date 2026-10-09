"""Public-content SELECT boundary: no activity/event/receipt payload access."""

import json
from uuid import UUID

from sqlalchemy import text

from .db_resource_pin_repository import scope_params


class DBExecutionContentRepository:
    def __init__(self, db_session):
        self.db_session = db_session

    async def get_snapshot(
        self, scope, content_id, run_id, step_id, formal_position, *, include_body=True
    ):
        columns = "c.body," if include_body else ""
        return (
            (
                await self.db_session.execute(
                    text(f"""SELECT c.content_id,{columns}c.content_digest,c.byte_length,c.redacted,c.citation_refs
          FROM execution_public_content c JOIN execution_content_bindings b USING(content_id)
          WHERE c.content_id=:id AND b.run_id=:run AND b.step_id=:step AND b.formal_position<=:position
          AND c.scope_key=:scope AND b.scope_key=:scope"""),
                    {
                        **scope_params(scope),
                        "id": UUID(content_id),
                        "run": run_id,
                        "step": step_id,
                        "position": formal_position,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )

    async def has_citation(self, scope, citation):
        return bool(
            await self.db_session.scalar(
                text("""SELECT EXISTS(SELECT 1 FROM execution_public_content c
          JOIN execution_content_bindings b USING(content_id)
          WHERE c.scope_key=:scope AND b.scope_key=:scope AND c.citation_refs @> CAST(:citation AS jsonb))"""),
                {**scope_params(scope), "citation": json.dumps([citation])},
            )
        )

    async def get_citation(self, scope, citation_id):
        return await self.db_session.scalar(
            text("""SELECT ref FROM execution_public_content c
          JOIN execution_content_bindings b USING(content_id)
          CROSS JOIN LATERAL jsonb_array_elements(c.citation_refs) ref
          WHERE c.scope_key=:scope AND b.scope_key=:scope AND ref->>'citation_id'=:id LIMIT 1"""),
            {**scope_params(scope), "id": citation_id},
        )
