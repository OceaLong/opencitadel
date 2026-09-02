"""Atomic application service for idempotent kernel commands."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts, DecisionRejected
from app.kernel.domain.events import replay
from app.kernel.domain.reducer import ReducerRegistry
from app.kernel.domain.state import RunState

from .ports import CommandResult, KernelAuthorization, KernelStore

DecisionFactsFactory = Callable[
    [CommandEnvelope, RunState | None], DecisionFacts | Awaitable[DecisionFacts]
]


class CommandService:
    def __init__(
        self,
        *,
        store: KernelStore,
        reducers: ReducerRegistry,
        facts_factory: DecisionFactsFactory,
    ) -> None:
        self._store = store
        self._reducers = reducers
        self._facts_factory = facts_factory

    async def submit(
        self,
        command: CommandEnvelope,
        authorization: KernelAuthorization,
    ) -> CommandResult:
        """Persist one command and its complete deterministic decision atomically."""

        if authorization.actor_user_id != command.actor_user_id and not (
            authorization.is_admin or authorization.is_system
        ):
            raise PermissionError("command actor does not match authorization")
        if not authorization.allows(command.owner_scope):
            raise PermissionError("command owner scope is not authorized")

        async with self._store.transaction(authorization) as transaction:
            existing = await transaction.get_command_result(command.command_id)
            if existing is not None:
                return existing
            concurrent_result = await transaction.reserve_command(command)
            if concurrent_result is not None:
                return concurrent_result
            events = await transaction.load_events(command.run_id)
            state = replay(events)
            resolved_facts = self._facts_factory(command, state)
            facts = await resolved_facts if inspect.isawaitable(resolved_facts) else resolved_facts
            try:
                await transaction.validate_command(command)
                decision = self._reducers.decide(state, command, facts)
            except DecisionRejected as exc:
                return await transaction.reject_command(
                    command,
                    code=exc.code,
                    message=str(exc),
                )
            return await transaction.append_decision(command, decision, facts)
