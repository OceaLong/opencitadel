"""Trusted isolated catalog dispatch. Never constructs an ordinary session catalog."""

from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID

from app.application.evaluation.environment_service import validate_environment
from app.application.execution.tool_catalog import CatalogSnapshot, ToolDefinition
from app.domain.evaluation.configuration import ConfigVersion, digest
from app.domain.evaluation.recording import validate_typed
from app.domain.execution.tool_contract import (
    RecordedContract,
    ToolContractChanged,
    execution_contract,
)
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

    async def access(
        self, context, *, tool=None, uow=None, locked=False, approval_subject_digest=None
    ):
        binding = (
            await uow.evaluation_environment.binding(context.run.owner_scope, context.run.run_id)
            if uow is not None
            else await self.binding(context.run)
        )
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
        async with (
            nullcontext(uow)
            if uow is not None
            else self.uow_factory(AuthorizationContext.for_principal(principal, scope=scope))
        ) as uow:
            repo = uow.evaluation_environment
            if locked:
                await uow.execution_usage.authorize_principal(scope, principal, lock=True)
            lease = await repo.lease(
                scope, binding["lease_id"], **({"lock": True} if locked else {})
            )
            if (
                lease.state != "leased"
                or lease.generation != binding["generation"]
                or lease.expires_at <= datetime.now(UTC)
            ):
                raise ValueError("environment_lease_unavailable")
            value = await repo.registered(scope, "environment", lease.environment_version)
            if locked:
                await repo.lock_registrations(
                    scope,
                    [
                        ("environment", lease.environment_version),
                        *[("target", ref.id) for ref in value.allowed_targets],
                        *[("credential", ref.id) for ref in value.credential_refs],
                    ],
                )
                value = await repo.registered(scope, "environment", lease.environment_version)
            adapter, targets, credentials = await validate_environment(
                uow, scope, principal, value, self.registry, ceiling=self.ceiling, lock=locked
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
            if locked and tool is not None:
                selected = next(c for c in admission["contracts"] if c["name"] == tool)
                await uow.execution_usage.authorize_tool(
                    scope,
                    skill_id=ConfigVersion.model_validate(config).selection.skill_id,
                    name=tool,
                    pack=selected["pack"],
                    connector_ids=(selected["connector_id"],)
                    if selected.get("connector_id")
                    else (),
                    lock=True,
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
                    scope,
                    context.run.run_id,
                    context.activity_id,
                    subject_digest=approval_subject_digest,
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
        adapter, lease, targets, _, admission, scope, principal = await self.access(context)
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
                    approval_feedback_param=policy.approval_feedback_param,
                    contract=_execution_contract(contract, adapter, lease, targets),
                )
            )
        return CatalogSnapshot(
            definitions=tuple(definitions),
            fingerprint=admission["config_fingerprint"],
            authority=_authority(scope, principal, lease, admission),
        )

    async def invoke(
        self,
        context,
        *,
        name,
        arguments,
        expected_fingerprint=None,
        approval_feedback=None,
        invocation=None,
        contract=None,
        execute=None,
    ):
        if invocation is None or contract is None:
            raise ToolContractChanged("invocation_binding_required")
        if approval_feedback:
            raise ToolContractChanged("approval_arguments_not_final")
        try:
            approval_digest = None if contract.is_pure_clarification else invocation.digest
            access = await self.access(context, tool=name, approval_subject_digest=approval_digest)
            adapter, lease, targets, credentials, admission, scope, principal = access
            expected = next(c for c in admission["contracts"] if c["name"] == name)
            current = _execution_contract(expected, adapter, lease, targets)
            if current.digest != contract.digest:
                raise ToolContractChanged()
            _verify_invocation(invocation, contract, arguments, scope, principal, lease, admission)
            validate_typed(arguments, contract.arguments_schema)
        except ToolContractChanged:
            raise
        except PermissionError as error:
            raise ToolContractChanged("authorization_changed") from error
        except (ValueError, StopIteration) as error:
            raise ToolContractChanged() from error
        if contract.requires_operation and execute is None:
            raise ToolContractChanged("effect_service_required")

        async def preflight(uow):
            try:
                fresh = await self.access(
                    context,
                    tool=name,
                    uow=uow,
                    locked=True,
                    approval_subject_digest=approval_digest,
                )
                new_adapter, new_lease, new_targets, _, new_admission, new_scope, new_principal = (
                    fresh
                )
                selected = next(c for c in new_admission["contracts"] if c["name"] == name)
                if (
                    _execution_contract(selected, new_adapter, new_lease, new_targets).digest
                    != contract.digest
                ):
                    raise ToolContractChanged()
                _verify_invocation(
                    invocation,
                    contract,
                    arguments,
                    new_scope,
                    new_principal,
                    new_lease,
                    new_admission,
                )
                if context.require_active_lease:
                    context.require_active_lease()
            except ToolContractChanged:
                raise
            except PermissionError as error:
                raise ToolContractChanged("authorization_changed") from error
            except (ValueError, StopIteration) as error:
                raise ToolContractChanged() from error

        async def send():
            if context.require_active_lease:
                context.require_active_lease()
            await adapter.check_runtime(lease)
            matching = [
                target for target in targets if any(c.name == name for c in target.contracts)
            ]
            if matching:
                if len(matching) != 1:
                    raise ValueError("environment_external_binding_ambiguous")
                target = matching[0]
                executor = self.registry.executors.get(target.id)
                if executor is None:
                    raise ValueError("environment_external_executor_unavailable")
                return await executor.bind(adapter, lease).invoke(
                    target, credentials, scope, principal, name, arguments
                )
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
                if context.require_active_lease:
                    context.require_active_lease()
                return (await pack.invoke(name, **arguments)).model_dump(mode="json")
            finally:
                sandbox = getattr(pack, "sandbox", None)
                if sandbox is not None and hasattr(sandbox, "client"):
                    await sandbox.client.aclose()

        if execute is not None:
            return await execute(send, preflight)
        async with self.uow_factory() as uow:
            await preflight(uow)
            await uow.commit()
        return await send()

    async def retrieval(self, context, query):
        await self.access(context)
        return {"query": query, "sources": []}

    async def remediation(self, context, *args, **kwargs):
        # No actuator is reachable unless its explicit target executor is bound. The
        # current conversational admission supports no remediation activity contract.
        await self.access(context)
        raise ValueError("environment_remediation_binding_unavailable")


def _authority(scope, principal, lease, admission):
    return {
        "scope": scope.model_dump(mode="json"),
        "authorization_epoch": digest(
            {
                "kind": "evaluation_environment",
                "principal": principal.model_dump(mode="json"),
                "lease_id": str(lease.id),
                "generation": lease.generation,
                "config_fingerprint": admission["config_fingerprint"],
            }
        ),
    }


def _execution_contract(item, adapter, lease, targets):
    selected = [c for target in targets for c in target.contracts if c.name == item["name"]]
    if len(selected) > 1:
        raise ValueError("environment_external_binding_ambiguous")
    base = (
        selected[0]
        if selected
        else RecordedContract(
            name=item["name"],
            pack=item["pack"],
            schema_body=item["schema"],
            policy=item["policy"],
            connector_id=item.get("connector_id"),
            binding_revision=item.get("binding_revision") or digest(item),
            authority_revision=item.get("authority_revision") or digest(item),
        )
    )
    value = execution_contract(base)
    revision = digest(
        {
            "contract": value.digest,
            "adapter_revision": adapter.revision,
            "environment_version": str(lease.environment_version),
            "lease_id": str(lease.id),
            "generation": lease.generation,
        }
    )
    return value.model_copy(update={"binding_revision": revision, "authority_revision": revision})


def _verify_invocation(subject, contract, arguments, scope, principal, lease, admission):
    authority = _authority(scope, principal, lease, admission)
    if subject.contract_digest != contract.digest or subject.arguments_digest != digest(arguments):
        raise ToolContractChanged()
    if (
        subject.scope_digest != digest(authority["scope"])
        or subject.authorization_epoch != authority["authorization_epoch"]
    ):
        raise ToolContractChanged("authorization_changed")
