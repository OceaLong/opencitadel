from types import SimpleNamespace
from uuid import uuid4

from app.application.execution.context_obligations import effect_obligations
from app.domain.execution.effect import EffectOperationSpec, EffectState
from tests.app.application.execution.test_context_compiler import BOUNDARY, compile, source


def test_unknown_or_abandoned_operation_is_protected_and_never_inferred_not_applied():
    spec = EffectOperationSpec(
        operation_id=uuid4(),
        invocation_id=uuid4(),
        run_id=uuid4(),
        activity_id=uuid4(),
        generation=0,
        provider_identity="fixture",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key="original-key",
        capability="manual_only",
    )
    state = SimpleNamespace(
        run_id=spec.run_id,
        stream_version=90,
        effect_operations=(EffectState(spec=spec, state="unknown", revision=3, abandoned=True),),
    )
    ledger = effect_obligations(state, scope_key=BOUNDARY.scope_key, epoch="1")
    assert ledger.protected
    assert str(spec.invocation_id) in ledger.messages[0].content
    assert str(spec.operation_id) in ledger.messages[0].content
    assert '"state":"unknown"' in ledger.messages[0].content
    assert '"state":"not_applied"' not in ledger.messages[0].content
    result = compile([ledger, *(source(i + 1, "historical fact " * 40) for i in range(70))])
    assert result.status == "needs_compaction"
    assert all(ledger.ref not in chunk.sources for chunk in result.compaction.chunks)
    assert effect_obligations(state, scope_key=BOUNDARY.scope_key, epoch="1") == ledger


def test_completed_effect_retains_original_identity_and_receipt_independent_of_summary_text():
    spec = EffectOperationSpec(
        operation_id=uuid4(),
        invocation_id=uuid4(),
        run_id=uuid4(),
        activity_id=uuid4(),
        generation=0,
        provider_identity="fixture",
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key="original-key",
        capability="manual_only",
    )
    receipt = uuid4()
    state = SimpleNamespace(
        run_id=spec.run_id,
        stream_version=100,
        effect_operations=(
            EffectState(spec=spec, state="applied", revision=4, receipt_ids=(receipt,)),
        ),
    )
    ledger = effect_obligations(state, scope_key=BOUNDARY.scope_key, epoch="1")
    result = compile([ledger])
    assert result.status == "ready"
    assert str(receipt) in result.manifest.messages[0].content
    assert '"state":"applied"' in result.manifest.messages[0].content
    assert ledger.ref in result.manifest.original_closure
