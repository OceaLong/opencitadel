"""Compaction uses the same durable physical budget boundary as other inference."""

from typing import Protocol

from app.domain.execution.context_summary import ContextSummaryProposal


class ContextSummaryProposalStore(Protocol):
    async def load(self, request, run) -> ContextSummaryProposal | None: ...

    async def save(self, proposal: ContextSummaryProposal, request, context) -> None:
        """Commit only with the current lease and matching settled physical receipts."""
        ...


class ContextSourceValidator(Protocol):
    async def validate(self, boundary, sources) -> None:
        """Recheck live authorization and original integrity before each send/read."""
        ...

    async def validate_in_uow(self, work, boundary, sources) -> None:
        """Hold current source/principal authorization locks until permit commit."""
        ...
