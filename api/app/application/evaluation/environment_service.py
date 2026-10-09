"""Scoped administrative registration and kernel-owned isolated lifecycle orchestration."""

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.application.evaluation.dataset_service import fingerprint
from app.domain.evaluation.environment import EnvironmentLease, transition
from app.domain.evaluation.errors import EnvironmentTransportUnknown
from app.domain.models.audit_log import AuditLog
from app.domain.models.authorization import AuthorizationContext


class EnvironmentService:
    def __init__(
        self, uow_factory, registry, *, ceiling=2, capacity_policy=None, cursor_secret=None
    ):
        from app.domain.evaluation.environment_capacity import EnvironmentCapacityPolicy

        self.cursor_secret = cursor_secret
        self.capacity_policy = capacity_policy or EnvironmentCapacityPolicy(workspace_limit=ceiling)
        self.uow_factory, self.registry, self.ceiling = uow_factory, registry, ceiling

    async def inventory(self, scope, principal):
        from app.application.evaluation.discovery import (
            EnvironmentInventory,
            InventoryAdapter,
            InventoryCredential,
            InventoryTarget,
        )
        from app.domain.evaluation.environment import ImageIdentity

        if not principal.is_admin:
            raise PermissionError("environment_admin_required")
        async with self.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            adapters = []
            for name, adapter in sorted(self.registry.adapters.items()):
                images = []
                for reference in sorted(getattr(adapter, "allowed_images", ())):
                    if reference.startswith("sha256:"):
                        images.append(ImageIdentity(kind="local_content_id", value=reference))
                    elif "@sha256:" in reference:
                        repository, value = reference.rsplit("@", 1)
                        images.append(
                            ImageIdentity(
                                kind="registry_digest", repository=repository, value=value
                            )
                        )
                adapters.append(
                    InventoryAdapter(
                        name=name,
                        revision=adapter.revision,
                        images=tuple(images),
                        fixtures=tuple(sorted(adapter.fixture_revisions)),
                        healthchecks=tuple(sorted(adapter.healthcheck_revisions)),
                    )
                )
            return EnvironmentInventory(
                targets=tuple(
                    InventoryTarget(id=v.id, revision=v.revision, kind=v.kind)
                    for v in self.registry.targets.values()
                    if v.enabled
                ),
                credentials=tuple(
                    InventoryCredential(id=v.id, revision=v.revision, target=v.target)
                    for v in self.registry.credentials.values()
                    if v.enabled
                ),
                adapters=tuple(adapters),
            )

    async def register_inventory(self, scope, principal, kind, identity, revision, *, request_id):
        if not principal.is_admin:
            raise PermissionError("environment_admin_required")
        if kind not in {"target", "credential"}:
            raise ValueError("invalid_inventory_kind")
        inventory = self.registry.targets if kind == "target" else self.registry.credentials
        value = inventory.get(str(identity))
        if value is None or value.revision != revision or not value.enabled:
            raise ValueError("test_inventory_binding_unavailable")
        return await self.register(
            scope, principal, kind, value, request_id=request_id, reuse_existing=True
        )

    async def list_versions(self, scope, principal, *, cursor=None, limit=50):
        from uuid import UUID

        from app.application.evaluation.discovery import EnvironmentChoice, EnvironmentPage
        from app.application.evaluation.discovery_cursor import decode, encode

        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_limit")
        context = ["environments", scope.model_dump(mode="json"), principal.user_id]
        after = UUID(decode(self.cursor_secret, context, cursor)) if cursor else None
        async with self.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            rows = await uow.evaluation_environment.list_versions(
                scope, after=after, limit=limit + 1
            )
            items = []
            for version in rows[:limit]:
                try:
                    adapter, targets, _ = await validate_environment(
                        uow, scope, principal, version, self.registry, ceiling=self.ceiling
                    )
                    tool_names = sorted(
                        set(getattr(adapter, "tool_names", ()))
                        | {c.name for target in targets for c in target.contracts}
                    )
                    items.append(
                        EnvironmentChoice(
                            version=version, qualified=True, tool_names=tuple(tool_names)
                        )
                    )
                except (ValueError, LookupError):
                    items.append(
                        EnvironmentChoice(
                            version=version, qualified=False, reason="environment_unavailable"
                        )
                    )
            return EnvironmentPage(
                items=tuple(items),
                next_cursor=encode(self.cursor_secret, context, str(rows[limit - 1].id))
                if len(rows) > limit
                else None,
            )

    async def register(self, scope, principal, kind, value, *, request_id, reuse_existing=False):
        if not principal.is_admin:
            raise PermissionError("environment_admin_required")
        if not request_id or len(request_id) > 255:
            raise ValueError("request_id_required")
        auth = AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        body = value.model_dump(mode="json")
        signature = fingerprint("environment.register", [kind, body])
        async with self.uow_factory(auth) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            await uow.evaluation_dataset.lock_request(scope, request_id)
            previous = await uow.evaluation_dataset.receipt(scope, request_id, signature)
            if previous:
                return previous["public"]
            if kind == "environment":
                _, _targets, _ = await validate_environment(
                    uow,
                    scope,
                    principal,
                    value,
                    self.registry,
                    ceiling=self.ceiling,
                    enforce_ceiling=True,
                )
            else:
                self.registry.qualify(kind, value)
                if kind == "credential":
                    await uow.evaluation_environment.registered(
                        scope, "target", value.target.id, value.target.revision
                    )
            existing = None
            if reuse_existing:
                from app.domain.evaluation.errors import DatasetNotFound

                await uow.evaluation_environment.lock(
                    f"registry:{'team:' + scope.team_id if scope.team_id else 'user:' + scope.user_id}:{kind}:{value.id}"
                )
                with suppress(DatasetNotFound):
                    existing = await uow.evaluation_environment.registered(
                        scope, kind, value.id, value.revision
                    )
            if existing != value:
                await uow.evaluation_environment.register(scope, kind, value)
            public = {"id": str(value.id), "kind": kind, "revision": value.revision}
            await uow.evaluation_dataset.save_receipt(
                scope,
                request_id,
                signature,
                {
                    "operation": "environment.register",
                    "audit_resource_id": str(value.id),
                    "revision": value.revision,
                    "kind": kind,
                    "public": public,
                },
            )
            await uow.audit.add_evaluation(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.environment.register",
                    resource_type="evaluation_environment",
                    resource_id=str(value.id),
                    request_id=request_id,
                    metadata={"revision": value.revision},
                ),
                authorization=auth,
            )
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            await uow.commit()
            return public

    async def repair(self, scope, principal, lease_id, *, request_id):
        if not principal.is_admin:
            raise PermissionError("environment_repair_admin_required")
        auth = AuthorizationContext.for_principal(principal, scope=scope, request_id=request_id)
        async with self.uow_factory(auth) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            await uow.evaluation_dataset.lock_request(scope, request_id)
            signature = fingerprint("environment.repair", [str(lease_id)])
            previous = await uow.evaluation_dataset.receipt(scope, request_id, signature)
            if previous:
                return previous["public"]
            lease = await uow.evaluation_environment.lease(scope, lease_id)
            if lease.state != "quarantine":
                raise ValueError("environment_repair_requires_quarantine")
            identity = uuid4()
            await uow.evaluation_environment.request_repair(scope, identity, lease, principal)
            public = {
                "id": str(identity),
                "lease_id": str(lease_id),
                "revision": lease.revision,
                "status": "queued",
            }
            await uow.evaluation_dataset.save_receipt(
                scope,
                request_id,
                signature,
                {
                    "operation": "environment.repair",
                    "audit_resource_id": str(identity),
                    "revision": lease.revision,
                    "public": public,
                },
            )
            await uow.audit.add_evaluation(
                AuditLog(
                    actor_user_id=principal.user_id,
                    team_id=scope.team_id,
                    action="evaluation.environment.repair",
                    resource_type="evaluation_environment",
                    resource_id=str(identity),
                    request_id=request_id,
                    metadata={"revision": lease.revision},
                ),
                authorization=auth,
            )
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            await uow.commit()
            return public

    async def version(self, scope, principal, identity):
        async with self.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=False)
            return await uow.evaluation_environment.registered(scope, "environment", identity)

    async def allocate_in_uow(
        self, uow, scope, principal, version_id, slot, *, lease_id=None, generation: int = 1
    ):
        value = await uow.evaluation_environment.registered(scope, "environment", version_id)
        _, targets, _ = await validate_environment(
            uow, scope, principal, value, self.registry, ceiling=self.ceiling
        )
        workspace = "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id
        if slot.workspace != workspace:
            raise ValueError("environment_slot_scope_mismatch")
        lease = EnvironmentLease(
            id=lease_id or uuid4(),
            environment_version=version_id,
            case_slot=slot,
            generation=generation,
            revision=1,
            state="allocated",
            requester=principal.model_dump(mode="json"),
            expires_at=datetime.now(UTC) + timedelta(seconds=value.limits.timeout_seconds),
        )
        lease = await uow.evaluation_environment.allocate(
            scope,
            lease,
            targets,
            concurrency=value.limits.concurrency,
            capacity_policy=self.capacity_policy,
        )
        if lease.state == "allocated":
            lease = await uow.evaluation_environment.begin(scope, lease, "preparing", "prepare")
        return lease

    async def cleanup_in_uow(self, uow, scope, lease_id, *, repair=False, principal=None):
        repo = uow.evaluation_environment
        lease = await repo.lease(scope, lease_id, lock=True)
        if lease.state in {"verified_clean", "cleaning"}:
            return lease
        if lease.state == "quarantine" and not repair:
            await repo.enqueue(scope, lease, "cleanup")
            return lease
        if lease.state == "preparing" and not repair:
            unknown = await repo.cancel_operations(scope, lease)
            if unknown:
                quarantined = transition(lease, "quarantine")
                await repo.save(
                    scope, lease, quarantined, error="environment_prepare_outcome_unknown"
                )
                await repo.enqueue(scope, quarantined, "cleanup")
                return quarantined
        if repair:
            if await repo.unresolved_operations(scope, lease.id):
                raise ValueError("environment_repair_unresolved_operation")
            if principal is None or not principal.is_admin:
                raise PermissionError("environment_repair_admin_required")
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
        return await repo.begin(
            scope,
            lease,
            "cleaning",
            "cleanup",
            repair=repair,
            administrator=bool(principal and principal.is_admin),
        )


async def validate_environment(
    uow, scope, principal, value, registry, *, ceiling=2, enforce_ceiling=False
):
    await uow.evaluation_dataset.authorize(scope, principal, write=False)
    if enforce_ceiling and value.limits.concurrency > ceiling:
        raise ValueError("environment_deployment_ceiling")
    repo = uow.evaluation_environment
    targets = tuple(
        [
            await repo.registered(scope, "target", ref.id, ref.revision)
            for ref in value.allowed_targets
        ]
    )
    credentials = tuple(
        [
            await repo.registered(scope, "credential", ref.id, ref.revision)
            for ref in value.credential_refs
        ]
    )
    for target in targets:
        registry.qualify("target", target)
        if target.kind in {"mcp", "a2a", "actuator"}:
            executor = registry.executors.get(target.id)
            if executor is None:
                raise ValueError("environment_external_executor_unavailable")
            executor.validate(target)
        if target.connector_id:
            await repo.validate_connector(scope, target)
    for credential in credentials:
        registry.qualify("credential", credential)
        if credential.target not in value.allowed_targets:
            raise ValueError("test_credential_target_mismatch")
    return registry.resolve(value, targets), targets, credentials


class EnvironmentWorker:
    """Each durable phase commits intent/claim before I/O; callbacks compare all fences."""

    def __init__(self, uow_factory, registry):
        self.uow_factory, self.registry = uow_factory, registry

    async def process(self, scope, operation_id):
        async with self.uow_factory() as uow:
            claimed = await uow.evaluation_environment.claim(scope, operation_id)
            if claimed is None:
                await uow.commit()
                return False
            operation, lease = claimed
            await uow.commit()
        try:
            async with self.uow_factory() as uow:
                value = await uow.evaluation_environment.registered(
                    scope, "environment", lease.environment_version
                )
                targets = tuple(
                    [
                        await uow.evaluation_environment.registered(
                            scope,
                            "target",
                            ref.id,
                            ref.revision,
                            current=operation.phase not in {"cleanup", "verify_clean"},
                        )
                        for ref in value.allowed_targets
                    ]
                )
                if operation.phase not in {"cleanup", "verify_clean"}:
                    from app.domain.models.scope import Principal

                    await uow.evaluation_dataset.authorize(
                        scope, Principal.model_validate(lease.requester), write=True
                    )
            adapter = self.registry.resolve(value, targets)
            action = getattr(
                adapter, "verify" if operation.phase.startswith("verify") else operation.phase
            )
            receipt = await action(lease, operation, value, targets)
            if operation.phase.startswith("verify") and receipt.get("verified") is not True:
                raise ValueError("environment_verification_failed")
            error = None
        except EnvironmentTransportUnknown:
            receipt, error = {}, "environment_unknown_operation"
        except (OSError, RuntimeError, ValueError, PermissionError):
            receipt, error = (
                {},
                (
                    "environment_unknown_operation"
                    if operation.phase == "prepare"
                    else "environment_" + operation.phase + "_failed"
                ),
            )
        async with self.uow_factory() as uow:
            accepted = await uow.evaluation_environment.complete(
                scope, operation, receipt, error=error
            )
            await uow.commit()
        return accepted
