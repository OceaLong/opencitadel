"""One context authority graph for kernel handlers, original reads and physical permits."""

from dataclasses import dataclass

from app.application.execution.activities.context_compact import ContextCompactActivityHandler
from app.application.execution.activities.context_prepare import ContextPrepareActivityHandler
from app.application.execution.context_read_source import ContextReadSource
from app.infrastructure.execution.context_contract_validation import ContextContractValidator
from app.infrastructure.execution.context_model_dispatch import (
    ContextModelDispatchValidator,
    PreparedContextValidation,
)
from app.infrastructure.execution.context_original_reader import ContextOriginalReader
from app.infrastructure.execution.context_preparation import PostgresContextPreparation
from app.infrastructure.execution.context_read_service import ContextReadService
from app.infrastructure.execution.context_source_validation import PostgresContextSourceValidator
from app.infrastructure.execution.context_summary_dispatch import ContextSummaryDispatchValidator
from app.infrastructure.execution.postgres_context_sources import PostgresContextSources
from app.infrastructure.execution.postgres_context_summary import DurableContextSummaryProposalStore


def context_admission_sources(*, signing_secret):
    """Borrow the locked admission UoW so the cut and CreateRun commit together."""
    return lambda work: PostgresContextSources(work.db_session, signing_secret=signing_secret)


@dataclass(frozen=True)
class ContextRuntimeServices:
    uow_factory: object
    objects: object
    storage: object
    sources: PostgresContextSourceValidator
    model_validator: ContextModelDispatchValidator
    summary_validator: ContextSummaryDispatchValidator
    prepared_validation: PreparedContextValidation
    reader: ContextReadService

    def handlers(self, *, definitions, tools, usage, models, client_factory, replay=None):
        return (
            ContextPrepareActivityHandler(
                objects=self.objects,
                definitions=definitions,
                preparation=PostgresContextPreparation(
                    uow_factory=self.uow_factory,
                    objects=self.objects,
                    storage=self.storage,
                    usage=usage,
                    tools=tools,
                    sources=self.sources,
                ),
            ),
            ContextCompactActivityHandler(
                objects=self.objects,
                definitions=definitions,
                proposals=DurableContextSummaryProposalStore(self.uow_factory),
                sources=self.sources,
                usage=usage,
                models=models,
                client_factory=client_factory,
                replay=replay,
            ),
        )


def build_context_runtime(*, uow_factory, objects, storage, signing_secret):
    sources = PostgresContextSourceValidator(uow_factory=uow_factory, storage=storage)
    return ContextRuntimeServices(
        uow_factory=uow_factory,
        objects=objects,
        storage=storage,
        sources=sources,
        model_validator=ContextModelDispatchValidator(objects, sources, ContextContractValidator()),
        summary_validator=ContextSummaryDispatchValidator(objects, sources),
        prepared_validation=PreparedContextValidation(sources),
        reader=ContextReadService(
            uow_factory=uow_factory,
            objects=objects,
            pages=ContextReadSource(
                reader=ContextOriginalReader(
                    uow_factory=uow_factory, sources=sources, objects=objects, storage=storage
                ),
                validator=sources,
                signing_secret=signing_secret,
            ),
        ),
    )
