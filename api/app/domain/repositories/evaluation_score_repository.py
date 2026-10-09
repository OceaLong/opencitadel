"""Score persistence borrows the caller transaction, including its audit append."""

from typing import Protocol


class EvaluationScoreRepository(Protocol):
    async def eligible(self, scope, principal, candidate, *, lock=False): ...
    async def revision(self, scope, batch_id): ...
    async def settled(self, scope, result_id, source): ...
    async def append(
        self,
        scope,
        principal,
        candidate,
        *,
        source,
        scores,
        expected_evaluation_revision,
        request_id,
        required_dimensions,
        applicable_dimensions,
        supersedes=None,
        judge_run_id=None,
    ): ...
    async def history(
        self,
        scope,
        batch_id,
        *,
        evaluation_revision,
        result_id=None,
        after_revision=0,
        after_dimension="",
        limit=None,
    ): ...
    async def heads(self, scope, batch_id, *, evaluation_revision): ...
