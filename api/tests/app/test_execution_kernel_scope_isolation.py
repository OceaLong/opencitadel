"""Per-scope projection failure isolation and quarantine (D12/K4-1).

One poison scope must never abort the pending-projection pass: its failure is
counted, its peers keep projecting, and after the streak threshold the scope is
durably quarantined through the owner-scope source (and re-offered no more).
"""

import pytest

from app.application.ports.execution import FormalProjectorResult
from app.domain.models.scope import OwnerScope
from app.execution_kernel import ExecutionKernelRuntime

_POISON = OwnerScope.personal("poison-user")
_HEALTHY = OwnerScope.personal("healthy-user")


class _Scopes:
    def __init__(self, scopes) -> None:
        self._scopes = list(scopes)
        self.quarantined: list[tuple[OwnerScope, str, int]] = []

    async def list_pending(self, *, limit):
        del limit
        excluded = {scope.user_id for scope, _reason, _count in self.quarantined}
        return tuple(scope for scope in self._scopes if scope.user_id not in excluded)

    async def quarantine(self, owner_scope, *, reason, error, failure_count):
        del error
        self.quarantined.append((owner_scope, reason, failure_count))


class _Projector:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_once(self, owner_scope, *, limit, through_position=None):
        del limit, through_position
        self.calls.append(owner_scope.user_id)
        if owner_scope.user_id == _POISON.user_id:
            raise ValueError("execution_run_projection state hash mismatch")
        return FormalProjectorResult(processed=1, last_position=10)

    async def rebuild(self, owner_scope, *, through_position=None, batch_size=1000):
        raise AssertionError("rebuild is not part of this scenario")


def _runtime(scopes, projector) -> ExecutionKernelRuntime:
    return ExecutionKernelRuntime(
        command_handler=None,
        inbox_worker=None,
        activity_worker=None,
        decision_worker=None,
        outbox_dispatcher=None,
        timer_dispatcher=None,
        projector=projector,
        owner_scopes=scopes,
        metrics=None,
        activity_registry=None,
    )


@pytest.mark.asyncio
async def test_poison_scope_never_starves_peers_and_gets_quarantined() -> None:
    scopes = _Scopes([_POISON, _HEALTHY])
    projector = _Projector()
    runtime = _runtime(scopes, projector)

    # Three consecutive passes: the poison scope fails each time, the healthy
    # scope keeps projecting each time (no starvation, no propagated error).
    for round_number in (1, 2, 3):
        result = await runtime.run_pending_projectors_once()
        assert result.processed == 1, f"round {round_number} lost the healthy scope"
        assert result.last_position == 10

    # The third failure crossed the threshold: quarantined exactly once.
    assert [(scope.user_id, reason, count) for scope, reason, count in scopes.quarantined] == [
        ("poison-user", "ValueError", 3)
    ]

    # Once quarantined, discovery stops offering the scope at all.
    projector.calls.clear()
    result = await runtime.run_pending_projectors_once()
    assert result.processed == 1
    assert projector.calls == ["healthy-user"]


@pytest.mark.asyncio
async def test_success_resets_the_failure_streak() -> None:
    scopes = _Scopes([_POISON])
    fail_rounds = {1, 2}
    round_counter = {"n": 0}

    class _FlakyProjector(_Projector):
        async def run_once(self, owner_scope, *, limit, through_position=None):
            del limit, through_position
            round_counter["n"] += 1
            if round_counter["n"] in fail_rounds:
                raise ValueError("transient corruption")
            return FormalProjectorResult(processed=1, last_position=5)

    runtime = _runtime(scopes, _FlakyProjector())

    await runtime.run_pending_projectors_once()  # failure 1
    await runtime.run_pending_projectors_once()  # failure 2
    await runtime.run_pending_projectors_once()  # success -> streak reset
    await runtime.run_pending_projectors_once()  # success

    assert scopes.quarantined == []
