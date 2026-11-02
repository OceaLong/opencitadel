"""Frozen tool contracts are checked again while physical admission owns locks."""

from app.domain.evaluation.errors import ReplayMismatch
from app.infrastructure.execution.context_authority import ContextRunAuthority


class ContextContractValidator:
    async def validate_in_uow(self, work, state, manifest):
        if len(manifest.tool_contracts) != len(manifest.tools):
            raise ValueError("CONTEXT_TOOL_CONTRACT_INCOMPLETE")
        scope, _ = await ContextRunAuthority(
            work.db_session, signing_secret=work.evaluation_budget.secret
        ).lock(manifest.boundary)
        if manifest.boundary.current_run_id != state.run_id:
            raise ValueError("CONTEXT_TOOL_CONTRACT_CHANGED")
        try:
            for contract in manifest.tool_contracts:
                if contract.contract_schema_version != 2:
                    raise ValueError("CONTEXT_TOOL_CONTRACT_INCOMPLETE")
                for identity, expected in sorted(contract.connector_bindings.items()):
                    actual = await work.evaluation_recording.connector_binding(
                        scope,
                        contract.pack,
                        identity,
                        lock=True,
                        execution=True,
                        tool_name=contract.source_name,
                    )
                    if actual != expected:
                        raise ValueError("CONTEXT_TOOL_CONTRACT_CHANGED")
        except (ReplayMismatch, PermissionError) as error:
            raise ValueError("CONTEXT_TOOL_CONTRACT_CHANGED") from error
