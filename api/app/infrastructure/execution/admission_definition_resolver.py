"""Resolve a head in the exact caller-owned CreateRun ingress transaction."""

from app.domain.execution.definition import DefinitionIntegrityError, DefinitionUnavailable
from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore


class AdmissionDefinitionResolver:
    async def __call__(
        self, *, family, scope, command_id, run_id, read_context=None, command_sink=None
    ):
        session = getattr(read_context, "db_session", None)
        sink_session = getattr(command_sink, "_session", None)
        if session is None:
            session = sink_session
        if session is None:
            raise DefinitionUnavailable("definition admission requires the ingress transaction")
        if sink_session is None:
            raise DefinitionUnavailable("definition admission requires a bound transaction sink")
        if sink_session is not session:
            raise DefinitionIntegrityError("definition pin and ingress use different transactions")
        if command_sink is None:
            raise DefinitionUnavailable("definition admission requires the transaction-owned sink")
        key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
        store = PostgresDefinitionStore(session)
        pin = await store.pin_admission(
            family=family, scope_key=key, command_id=command_id, run_id=run_id
        )
        return pin, await store.manifest(pin)
