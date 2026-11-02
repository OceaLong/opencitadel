"""External effect identities and receipts survive compaction deterministically."""

from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)
from app.domain.execution.serialization import canonical_json_bytes


def effect_obligations(state, *, scope_key, epoch):
    if not state.effect_operations:
        return None
    facts = [
        {
            "operation_id": str(value.spec.operation_id),
            "invocation_id": str(value.spec.invocation_id),
            "activity_id": str(value.spec.activity_id),
            "generation": value.spec.generation,
            "provider_identity": value.spec.provider_identity,
            "provider_locator": value.spec.provider_locator,
            "contract_digest": value.spec.contract_digest,
            "arguments_digest": value.spec.arguments_digest,
            "target_digest": value.spec.target_digest,
            "state": value.state,
            "revision": value.revision,
            "receipt_ids": [str(identity) for identity in value.receipt_ids],
            "result_ref": value.result_ref,
            "awaiting_result": value.awaiting_result,
            "abandoned": value.abandoned,
            "integrity_conflict": value.integrity_conflict,
        }
        for value in sorted(state.effect_operations, key=lambda item: str(item.spec.operation_id))
    ]
    message = ContextMessage(
        role="user",
        content="Formal external operation ledger; cancellation or abandonment does not prove non-application. Reconcile pending/unknown operations before any retry.\n"
        + canonical_json_bytes(facts).decode(),
    )
    digest = context_digest([message.model_dump(mode="json")])
    return ContextSource(
        ref=ContextSourceRef(
            source_id=f"effects:{state.run_id}:{state.stream_version}",
            scope_key=scope_key,
            run_id=state.run_id,
            kind="tool",
            digest=digest,
            formal_position=0,
            authorization_epoch=epoch,
        ),
        messages=(message,),
        messages_digest=digest,
        protected=True,
    )
