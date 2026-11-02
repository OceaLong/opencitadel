"""Owned acceptance adapter for querying a deliberately withheld shell receipt.

The real sandbox HTTP write and receipt-loss fault stay unchanged. This adapter
only advertises query support in the focused profile and binds each query to
that fault's retained identity. Production shell tools never claim this support.
"""

import functools
import os


def install(control, log):
    if os.environ.get("ACCEPTANCE_PHASE1_BROWSER") != "1":
        return
    from app.application.evaluation.contract_capture import ContractCapture
    from app.composition import effects
    from app.domain.execution.tool_contract import EffectContract

    describe = ContractCapture.describe

    @functools.wraps(describe)
    async def contracts(self, context, built, **kwargs):
        values = await describe(self, context, built, **kwargs)
        return tuple(
            value.model_copy(
                update={
                    "implementation_revision": "acceptance-shell-query-v1",
                    "effect_contract": EffectContract(capability="resolve_only", resolve=True),
                }
            )
            if value.pack == "shell" and value.name == "shell_execute"
            else value
            for value in values
        )

    ContractCapture.describe = contracts
    build = effects.build_effect_reconciler

    @functools.wraps(build)
    def reconciler(*, resources, shared, service):
        result = build(resources=resources, shared=shared, service=service)

        async def query(spec, scope):
            arm = control.read("arm.json")
            if (
                arm is None
                or arm["kind"] != "shell_receipt_loss"
                or arm["boot_id"] != log.boot_id
                or arm["source_sha256"] != log.binding["source_sha256"]
                or arm["execution_run_id"] != str(spec.run_id)
                or arm["activity_id"] != str(spec.activity_id)
                or arm["owner_user_id"] != scope.user_id
                or scope.team_id is not None
                or spec.provider_locator.get("tool_name") != "shell_execute"
            ):
                raise ValueError("query requires the exact owned receipt-loss operation")
            receipt = control.read("receipt-" + arm["fault_id"] + ".json")
            if receipt is None:
                raise ValueError("physical receipt is not durably retained")
            sandbox = await shared.sandbox_factory.get(arm["sandbox_id"])
            if sandbox is None:
                raise ValueError("owned sandbox unavailable for query")
            try:
                witness = await sandbox.read_file(arm["marker_path"], max_length=128)
            finally:
                await sandbox.client.aclose()
            if not witness.success or witness.data.get("content") != "owned-write\n":
                raise ValueError("query witness changed")
            # The provider query still withholds its original receipt. The marker
            # alone is intentionally insufficient to settle the formal operation.
            from physical_faults import digest

            evidence = {
                "operation_id": str(spec.operation_id),
                "provider": "acceptance-shell-query-v1",
                "fault_id": arm["fault_id"],
                "receipt_sha256": digest(receipt),
                "receipt_withheld": True,
                "witness_lines": 1,
            }
            with control.lock():
                previous = control.read("queries-" + arm["fault_id"] + ".json") or []
                control.write("queries-" + arm["fault_id"] + ".json", [*previous, evidence])
            return {"state": "unknown", "evidence": evidence}

        result.resolvers["shell"] = query
        return result

    effects.build_effect_reconciler = reconciler
