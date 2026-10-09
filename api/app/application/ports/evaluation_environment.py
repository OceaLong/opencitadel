"""Only trusted, registered lifecycle implementations can provision case resources."""

from typing import Protocol

from app.domain.evaluation.environment import (
    EnvironmentLease,
    EnvironmentOperation,
    EnvironmentVersion,
    TestTarget,
)


class EnvironmentAdapter(Protocol):
    revision: str
    fixture_revisions: frozenset[str]
    healthcheck_revisions: frozenset[str]

    def validate(self, version: EnvironmentVersion, targets: tuple[TestTarget, ...]) -> None: ...
    async def prepare(
        self,
        lease: EnvironmentLease,
        operation: EnvironmentOperation,
        version: EnvironmentVersion,
        targets: tuple[TestTarget, ...],
    ) -> dict: ...
    async def reset(
        self,
        lease: EnvironmentLease,
        operation: EnvironmentOperation,
        version: EnvironmentVersion,
        targets: tuple[TestTarget, ...],
    ) -> dict: ...
    async def verify(
        self,
        lease: EnvironmentLease,
        operation: EnvironmentOperation,
        version: EnvironmentVersion,
        targets: tuple[TestTarget, ...],
    ) -> dict: ...
    async def cleanup(
        self,
        lease: EnvironmentLease,
        operation: EnvironmentOperation,
        version: EnvironmentVersion,
        targets: tuple[TestTarget, ...],
    ) -> dict: ...


class AdapterRegistry:
    def __init__(
        self, adapters=(), credential_resolvers=(), *, targets=(), credentials=(), executors=()
    ):
        self.adapters = dict(adapters)
        self.executors = dict(executors)
        self.targets = {str(value.id): value for value in targets}
        self.credentials = {str(value.id): value for value in credentials}
        self.credential_resolvers = dict(credential_resolvers)

    def resolve(self, version, targets):
        adapter = self.adapters.get(version.reset_adapter)
        if adapter is None or adapter.revision != version.adapter_revision:
            raise ValueError("environment_adapter_unavailable")
        if (
            version.fixture_revision not in adapter.fixture_revisions
            or version.healthcheck_revision not in adapter.healthcheck_revisions
        ):
            raise ValueError("environment_fixture_unavailable")
        adapter.validate(version, targets)
        return adapter

    def qualify(self, kind, value):
        inventory = self.targets if kind == "target" else self.credentials
        expected = inventory.get(str(value.id))
        if expected is None or value != expected:
            raise ValueError("test_inventory_binding_unavailable")
        if kind == "credential" and value.resolver not in self.credential_resolvers:
            raise ValueError("test_credential_resolver_unavailable")

    def validate_tools(self, adapter, config, targets, *, environment_id):
        supported = frozenset(getattr(adapter, "tool_names", ()))
        external = {
            contract.name
            for target in targets
            for contract in target.contracts
            if target.id in getattr(self, "executors", {})
        }
        if set(config.selection.tool_names) - supported - external or config.selection.resources:
            raise ValueError("environment_tool_binding_unavailable")

        reference = config.selection.external_contract_ref
        if reference is not None and (
            reference.kind != "environment" or reference.version_id != environment_id
        ):
            raise ValueError("environment_reference_mismatch")
        for snapshot in config.snapshot["contracts"]:
            if snapshot["pack"] not in {"mcp", "a2a"}:
                continue
            matching = [
                c for target in targets for c in target.contracts if c.name == snapshot["name"]
            ]
            if reference is None or len(matching) != 1:
                raise ValueError("environment_external_contract_changed")
            current = matching[0].model_dump(mode="json")
            # Stored target contracts also carry transport-specific source names.
            expected = {
                key: current[key]
                for key in (
                    "name",
                    "pack",
                    "policy",
                    "connector_id",
                    "binding_revision",
                    "authority_revision",
                )
            }
            expected["schema"] = current["schema_body"]
            if expected != snapshot:
                raise ValueError("environment_external_contract_changed")
