"""Current scoped recording authority shared by replay and metadata-only preflight."""

from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.application.evaluation.configuration_metadata import ExternalToolContract
from app.application.evaluation.preflight import DependencyEvidence
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal


@dataclass(frozen=True)
class RecordingAccess:
    scope: object
    manifest: object
    repo: object
    uow: object
    authorization: object
    admission: dict


async def validate_recording(uow, scope, principal, version_id):
    await uow.evaluation_dataset.authorize(scope, principal, write=False)
    repo = uow.evaluation_recording
    manifest = await repo.version(scope, version_id)
    pins = await uow.resource_pins.validate(
        scope, "recording_version", str(version_id), manifest.pins
    )
    if any(not pin.available for pin in pins):
        raise ReplayMismatch("recording_source_unavailable")
    for contract in manifest.contracts:
        if contract.pack in {"mcp", "a2a"}:
            if not contract.connector_id or not contract.connector_bindings:
                raise ReplayMismatch("connector_unavailable")
            for connector_id, revision in contract.connector_bindings.items():
                if await repo.connector_binding(scope, contract.pack, connector_id) != revision:
                    raise ReplayMismatch("connector_changed")
        elif contract.pack != "retrieval":
            import inspect

            from app.application.evaluation.configuration_metadata import BUILTINS

            methods = [
                method
                for cls in BUILTINS
                for _, method in inspect.getmembers_static(cls, inspect.isfunction)
                if getattr(method, "_tool_name", None) == contract.name
            ]
            if (
                len(methods) != 1
                or methods[0]._tool_schema != contract.schema_body
                or methods[0]._tool_policy != contract.policy
            ):
                raise ReplayMismatch("builtin_contract_changed")
    return manifest


class RecordingAuthority:
    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def binding(self, run):
        # Kernel UoW is read only here. No request boolean can select this path.
        async with self.uow_factory() as uow:
            return await uow.evaluation_recording.binding(run.owner_scope, run.run_id)

    @asynccontextmanager
    async def open(self, context):
        binding = await self.binding(context.run)
        if binding is None:
            raise ReplayMismatch("replay_binding_missing")
        principal = Principal.model_validate(binding["principal"])
        original = context.run.owner_scope
        scope = (
            OwnerScope.team(principal.user_id, original.team_id)
            if original.team_id
            else OwnerScope.personal(principal.user_id)
        )
        if original.team_id is None and original.user_id != principal.user_id:
            raise ReplayMismatch("replay_scope_changed")
        auth = AuthorizationContext.for_principal(principal, scope=scope)
        async with self.uow_factory(auth) as uow:
            try:
                manifest = await validate_recording(uow, scope, principal, binding["version_id"])
            except PermissionError as error:
                raise ReplayMismatch("recording_authority_revoked") from error
            admission = binding.get("admission") or {}
            if (
                admission.get("source_entity_type") != "evaluation_recorded_case"
                or getattr(context.run, "source_entity_type", None) != "evaluation_recorded_case"
                or admission.get("source_entity_id")
                != getattr(context.run, "source_entity_id", None)
                or admission.get("policy_digest") != context.run.policy_snapshot.snapshot_digest
                or admission.get("purpose") != "evaluation_subject"
            ):
                raise ReplayMismatch("replay_admission_binding_invalid")
            from uuid import UUID

            config = await uow.evaluation_configuration.get_version(
                scope, "config", UUID(admission["config_version_id"])
            )
            if config["fingerprint"] != admission.get("config_fingerprint") or list(
                config["selection"]["tool_names"]
            ) != admission.get("tool_names"):
                raise ReplayMismatch("replay_configuration_changed")
            yield RecordingAccess(scope, manifest, uow.evaluation_recording, uow, auth, admission)

    async def approve(self, access, context, policy):
        if policy.requires_approval() and not await access.repo.approved(
            access.scope, context.run.run_id, context.activity_id
        ):
            raise ReplayMismatch("current_approval_required")


class RecordingExternalContracts:
    """Bound to its caller's UoW; never checks out another connection."""

    def __init__(self, uow):
        self.uow = uow

    async def contracts(self, scope, principal, names, *, reference):
        if reference.kind != "recording":
            raise ValueError("contract_unavailable")
        manifest = await validate_recording(self.uow, scope, principal, reference.version_id)
        selected = {
            c.name: c for c in manifest.contracts if c.name in names and c.pack in {"mcp", "a2a"}
        }
        if set(names) != selected.keys():
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
            for c in selected.values()
        )


class RecordingPreflightAuthority:
    async def check_in_uow(self, scope, suite, configs, *, for_start, uow, principal):
        references = set(suite.recording_versions)
        if not references:
            return DependencyEvidence(errors=("recording_reference_missing",))
        for config in configs:
            reference = config.selection.external_contract_ref
            if reference is not None and (
                reference.kind != "recording" or reference.version_id not in references
            ):
                return DependencyEvidence(errors=("recording_reference_mismatch",))
        try:
            manifests = [
                await validate_recording(uow, scope, principal, ref)
                for ref in sorted(references, key=str)
            ]
        except (ReplayMismatch, PermissionError):
            return DependencyEvidence(errors=("recording_unavailable",))
        return DependencyEvidence(
            ready=True, revision=",".join(str(manifest.id) for manifest in manifests)
        )
