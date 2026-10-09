"""Trusted isolated catalog dispatch. Never constructs an ordinary session catalog."""

from datetime import UTC, datetime
from uuid import UUID

from app.application.evaluation.environment_service import validate_environment
from app.application.execution.tool_catalog import CatalogSnapshot, ToolDefinition
from app.domain.evaluation.configuration import ConfigVersion
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.services.tools.base import ToolExecutionPolicy


class EnvironmentRuntime:
    def __init__(self, uow_factory, registry, sandbox_factory, *, ceiling=2):
        self.uow_factory, self.registry, self.sandbox_factory, self.ceiling = (
            uow_factory,
            registry,
            sandbox_factory,
            ceiling,
        )

    async def binding(self, run):
        async with self.uow_factory() as uow:
            return await uow.evaluation_environment.binding(run.owner_scope, run.run_id)

    async def active(self, context):
        binding = await self.binding(context.run)
        if (
            binding is None
            and getattr(context.run, "source_entity_type", None) == "evaluation_isolated_case"
        ):
            raise ValueError("environment_binding_missing")
        return binding is not None

    async def access(self, context, *, tool=None):
        binding = await self.binding(context.run)
        if binding is None:
            raise ValueError("environment_binding_missing")
        principal = Principal.model_validate(binding["principal"])
        original = context.run.owner_scope
        scope = (
            OwnerScope.team(principal.user_id, original.team_id)
            if original.team_id
            else OwnerScope.personal(principal.user_id)
        )
        if not original.team_id and principal.user_id != original.user_id:
            raise PermissionError("environment_scope_changed")
        admission = binding["admission"]
        if (
            admission["source_entity_type"] != getattr(context.run, "source_entity_type", None)
            or admission["source_entity_id"] != getattr(context.run, "source_entity_id", None)
            or admission["policy_digest"] != context.run.policy_snapshot.snapshot_digest
        ):
            raise ValueError("environment_admission_binding_invalid")
        async with self.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as uow:
            repo = uow.evaluation_environment
            lease = await repo.lease(scope, binding["lease_id"])
            if (
                lease.state != "leased"
                or lease.generation != binding["generation"]
                or lease.expires_at <= datetime.now(UTC)
            ):
                raise ValueError("environment_lease_unavailable")
            value = await repo.registered(scope, "environment", lease.environment_version)
            adapter, targets, credentials = await validate_environment(
                uow, scope, principal, value, self.registry, ceiling=self.ceiling
            )
            config = await uow.evaluation_configuration.get_version(
                scope, "config", UUID(admission["config_version_id"])
            )
            if config["fingerprint"] != admission["config_fingerprint"]:
                raise ValueError("environment_configuration_changed")
            self.registry.validate_tools(
                adapter, ConfigVersion.model_validate(config), targets, environment_id=value.id
            )
            await self.validate_current_tool_policy(
                uow, scope, ConfigVersion.model_validate(config)
            )
            if tool is not None:
                if tool not in admission["tool_names"]:
                    raise ValueError("environment_tool_not_selected")
                contracts = [
                    contract for contract in admission["contracts"] if contract["name"] == tool
                ]
                if len(contracts) != 1:
                    raise ValueError("environment_contract_unavailable")
                policy = ToolExecutionPolicy.model_validate(contracts[0]["policy"])
                if policy.requires_approval() and not await uow.evaluation_recording.approved(
                    scope, context.run.run_id, context.activity_id
                ):
                    raise PermissionError("current_approval_required")
        return adapter, lease, targets, credentials, admission, scope, principal

    @staticmethod
    async def validate_current_tool_policy(uow, scope, config):
        from app.domain.models.session_mode import SessionMode
        from app.domain.services.tools.capability_policy import CapabilityPolicy

        skill = None
        if config.selection.skill_id:
            skill = await uow.skill.get_by_id(config.selection.skill_id, scope=scope)
            if skill is None or not skill.enabled or skill.override_base_rules:
                raise PermissionError("environment_current_tool_policy_denied")
        policy = CapabilityPolicy.for_mode(
            SessionMode(config.selection.mode), skill.allowed_tools if skill else None
        )
        for contract in config.snapshot["contracts"]:
            execution = ToolExecutionPolicy.model_validate(contract["policy"])
            external = contract["pack"] in {"mcp", "a2a"}
            allowed = policy.allows_integration if external else policy.allows
            if not allowed(execution, tool_name=contract["name"]):
                raise PermissionError("environment_current_tool_policy_denied")
            if (
                external
                and skill
                and contract["connector_id"]
                not in (
                    skill.mcp_server_refs if contract["pack"] == "mcp" else skill.a2a_server_refs
                )
            ):
                raise PermissionError("environment_current_tool_policy_denied")

    async def definitions(self, context):
        _, _, _, _, admission, _, _ = await self.access(context)
        definitions = []
        for contract in admission["contracts"]:
            policy = ToolExecutionPolicy.model_validate(contract["policy"])
            definitions.append(
                ToolDefinition(
                    name=contract["name"],
                    tool_schema=contract["schema"],
                    requires_approval=policy.requires_approval(),
                    risk_summary=f"{policy.effect.value}: {contract['name']}",
                    approval_kind=policy.approval_kind,
                    approval_prompt_param=policy.approval_prompt_param,
                    approval_choices_param=policy.approval_choices_param,
                )
            )
        return CatalogSnapshot(
            definitions=tuple(definitions), fingerprint=admission["config_fingerprint"]
        )

    async def invoke(
        self, context, *, name, arguments, expected_fingerprint=None, approval_feedback=None
    ):
        adapter, lease, targets, credentials, admission, scope, principal = await self.access(
            context, tool=name
        )
        if (
            expected_fingerprint is not None
            and expected_fingerprint != admission["config_fingerprint"]
        ):
            raise ValueError("environment_catalog_changed")
        expected = next(c for c in admission["contracts"] if c["name"] == name)
        policy = ToolExecutionPolicy.model_validate(expected["policy"])
        if approval_feedback and policy.approval_feedback_param:
            arguments = {**arguments, policy.approval_feedback_param: approval_feedback}
        await adapter.check_runtime(lease)
        matching = [
            target
            for target in targets
            if any(contract.name == name for contract in target.contracts)
        ]
        if matching:
            if len(matching) != 1:
                raise ValueError("environment_external_binding_ambiguous")
            target = matching[0]
            executor = self.registry.executors.get(target.id)
            if executor is None:
                raise ValueError("environment_external_executor_unavailable")
            executor = executor.bind(adapter, lease)
            return await executor.invoke(target, credentials, scope, principal, name, arguments)
        if name not in adapter.tool_names:
            raise ValueError("environment_tool_binding_unavailable")
        pack = await adapter.tool_pack(lease, name, targets, self.sandbox_factory)
        try:
            descriptor = next(d for d in pack.get_tool_descriptors() if d.name == name)
            if (
                descriptor.schema != expected["schema"]
                or descriptor.policy.model_dump(mode="json") != expected["policy"]
            ):
                raise ValueError("environment_tool_contract_changed")
            result = await pack.invoke(name, **arguments)
            return result.model_dump(mode="json")
        finally:
            sandbox = getattr(pack, "sandbox", None)
            if sandbox is not None and hasattr(sandbox, "client"):
                await sandbox.client.aclose()

    async def retrieval(self, context, query):
        await self.access(context)
        return {"query": query, "sources": []}

    async def remediation(self, context, *args, **kwargs):
        # No actuator is reachable unless its explicit target executor is bound. The
        # current conversational admission supports no remediation activity contract.
        await self.access(context)
        raise ValueError("environment_remediation_binding_unavailable")
