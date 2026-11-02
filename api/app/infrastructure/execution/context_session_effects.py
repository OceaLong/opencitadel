"""Carry earlier external operations across a Session's accepted formal cut."""

from sqlalchemy import text

from app.application.execution.context_obligations import effect_obligations
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


async def session_effect_obligations(work, state, boundary, *, epoch):
    if state.source_entity_type != "session":
        return ()
    # Candidate discovery also uses formal facts: an asynchronously lagging Run
    # projection must never hide an operation accepted before the admission cut.
    rows = (
        await work.db_session.execute(
            text("""
SELECT origin.stream_id,(
 SELECT max(e.stream_version) FROM execution_events e
 WHERE e.owner_scope_key=:scope AND e.stream_type='run'
 AND e.stream_id=origin.stream_id AND e.position<=:through
) AS version
 FROM execution_events origin
 WHERE origin.owner_scope_key=:scope AND origin.stream_type='run' AND origin.stream_version=1
 AND origin.event_type='RunCreated' AND origin.stream_id<>CAST(CAST(:current AS uuid) AS text)
 AND origin.public_payload->>'source_entity_type'='session'
 AND origin.public_payload->>'source_entity_id'=:collection
 AND EXISTS (
 SELECT 1 FROM execution_events effect WHERE effect.owner_scope_key=:scope
 AND effect.stream_type='run' AND effect.stream_id=origin.stream_id
 AND effect.event_type='EffectOperationPrepared' AND effect.position<=:through
 ) ORDER BY origin.stream_id LIMIT 129
"""),
            {
                "scope": boundary.scope_key,
                "collection": boundary.collection_id,
                "current": state.run_id,
                "through": boundary.through_position,
            },
        )
    ).all()
    if len(rows) > 128:
        raise ValueError("CONTEXT_EFFECT_SOURCE_LIMIT")
    sources = []
    for identity, version in rows:
        prior = (
            await PostgresVerifiedRunLoader(work.db_session).load(
                identity, owner_scope=(state.owner_user_id, state.team_id), at_version=version
            )
        ).replay.state
        if (
            prior.source_entity_type != "session"
            or prior.source_entity_id != boundary.collection_id
        ):
            raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        source = effect_obligations(prior, scope_key=boundary.scope_key, epoch=epoch)
        if source is not None:
            sources.append(source)
    return tuple(sources)
