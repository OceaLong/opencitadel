"""Metadata-only environment authority composed by the fixed reference source kind."""

from app.application.evaluation.configuration_metadata import ExternalToolContract
from app.application.evaluation.environment_service import validate_environment
from app.application.evaluation.preflight import DependencyEvidence
from app.domain.evaluation.errors import DatasetNotFound


class EnvironmentExternalContracts:
    def __init__(self, uow, registry, *, ceiling=2):
        self.uow, self.registry, self.ceiling = uow, registry, ceiling

    async def contracts(self, scope, principal, names, *, reference):
        if reference.kind != "environment":
            raise ValueError("contract_unavailable")
        value = await self.uow.evaluation_environment.registered(
            scope, "environment", reference.version_id
        )
        _, targets, _ = await validate_environment(
            self.uow, scope, principal, value, self.registry, ceiling=self.ceiling
        )
        selected = [
            contract
            for target in targets
            for contract in target.contracts
            if contract.name in names
        ]
        if len(selected) != len(names) or {contract.name for contract in selected} != set(names):
            raise ValueError("contract_unavailable")
        return tuple(
            ExternalToolContract(
                name=c.name,
                pack=c.pack,
                schema_body=c.schema_body,
                policy=c.policy,
                connector_id=c.connector_id,
                binding_revision=c.binding_revision,
                authority_revision=c.authority_revision,
            )
            for c in selected
        )


class EvaluationExternalContracts:
    def __init__(self, uow, registry, *, ceiling=2):
        from app.application.evaluation.recording_authority import RecordingExternalContracts

        self.sources = {
            "recording": RecordingExternalContracts(uow),
            "environment": EnvironmentExternalContracts(uow, registry, ceiling=ceiling),
        }

    async def contracts(self, scope, principal, names, *, reference):
        return await self.sources[reference.kind].contracts(
            scope, principal, names, reference=reference
        )


class EnvironmentPreflightAuthority:
    def __init__(self, registry, *, ceiling=2):
        self.registry, self.ceiling = registry, ceiling

    async def check_in_uow(self, scope, suite, configs, *, for_start, uow, principal):
        try:
            value = await uow.evaluation_environment.registered(
                scope, "environment", suite.environment_version
            )
            adapter, targets, _ = await validate_environment(
                uow, scope, principal, value, self.registry, ceiling=self.ceiling
            )
            for config in configs:
                reference = config.selection.external_contract_ref
                if reference is not None and (
                    reference.kind != "environment" or reference.version_id != value.id
                ):
                    raise ValueError("environment_reference_mismatch")
                self.registry.validate_tools(adapter, config, targets, environment_id=value.id)
        except (ValueError, PermissionError, DatasetNotFound):
            return DependencyEvidence(errors=("environment_unavailable",))
        return DependencyEvidence(ready=True, revision=f"{value.id}:{value.revision}")
