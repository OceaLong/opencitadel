"""Transactional effect indexes and recovery permits. Run events remain authoritative."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.errors import ConflictError, ForbiddenError, NotFoundError
from app.domain.execution.effect import (
    EFFECT_COMMAND_MODELS,
    EFFECT_EVENT_TYPES,
    EffectState,
    evolve_effect,
)
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.models.authorization import AuthorizationContext, AuthorizationMode
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.execution.effect_models import operations, receipts, requests
from app.infrastructure.execution.models import ExecutionActivityTaskORM, ExecutionEventORM
from app.infrastructure.execution.postgres_activity_fence import ActivityLeaseExpiredError
from app.infrastructure.security.db_authorization import configure_session_authorization

EFFECT_COMMANDS = frozenset(EFFECT_COMMAND_MODELS)
_FENCES_KEY = "execution_effect_command_fences"


@dataclass(frozen=True)
class _EffectFence:
    transaction: object
    deadline: datetime


def _remember_effect_fence(session, command, deadline):
    session.info.setdefault(_FENCES_KEY, {})[command.command_id] = _EffectFence(
        transaction=session.sync_session.get_transaction(), deadline=deadline
    )


async def ensure_effect_command_current(session, command, *, if_issued=False):
    """Recheck the original locked lease/permit after all transactional writes."""
    if command.command_type not in EFFECT_COMMANDS:
        return
    fence = session.info.get(_FENCES_KEY, {}).get(command.command_id)
    if if_issued and (
        fence is None or fence.transaction is not session.sync_session.get_transaction()
    ):
        # A completed inbox replay wrote no new facts and issued no new permit.
        return
    if (
        fence is None
        or fence.transaction is not session.sync_session.get_transaction()
        or fence.deadline <= await session.scalar(select(func.clock_timestamp()))
    ):
        raise ActivityLeaseExpiredError("effect authority expired before commit")


def digest(value):
    return sha256(canonical_json_bytes(value)).hexdigest()


def scope_values(scope):
    return {
        "scope_key": f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
        "owner_user_id": None if scope.team_id else scope.user_id,
        "team_id": scope.team_id,
    }


async def lock_scope(session, owner_user_id, team_id):
    from app.infrastructure.execution.postgres_event_store import PostgresEventStore

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": PostgresEventStore._scope_advisory_lock_key(owner_user_id, team_id)},
    )


async def authorize_actor(session, scope, principal, scope_reason=None, *, write=True):
    """Read current authority, locking it through acceptance/consumption commit."""
    # PostgreSQL applies UPDATE RLS to SELECT FOR SHARE. Identity writes are
    # system-only, so even a caller's own visible row cannot be locked under
    # user authorization. Elevate only these fixed identity reads, retaining
    # their locks until the surrounding acceptance transaction commits.
    user_mode = await session.scalar(text("SELECT current_setting('app.auth_mode',true)"))
    if user_mode == "user":
        await configure_session_authorization(
            session, AuthorizationContext.system("effect-authority-check")
        )
    try:
        user = (
            (
                await session.execute(
                    text(
                        "SELECT status,token_version,global_role FROM users WHERE id=:id FOR SHARE"
                    ),
                    {"id": principal.user_id},
                )
            )
            .mappings()
            .first()
        )
        role = None
        if scope.team_id:
            role = await session.scalar(
                text(
                    "SELECT role FROM team_members WHERE team_id=:team AND user_id=:user FOR SHARE"
                ),
                {"team": scope.team_id, "user": principal.user_id},
            )
    finally:
        if user_mode == "user":
            await configure_session_authorization(
                session,
                AuthorizationContext(mode=AuthorizationMode.USER, principal=principal, scope=scope),
            )
    if (
        not user
        or user["status"] != "active"
        or user["token_version"] != principal.token_version
        or user["global_role"] != principal.global_role
    ):
        raise ForbiddenError("Effect actor authority changed")
    if write and principal.is_auditor:
        raise ForbiddenError("Auditors cannot resolve external effects")
    if principal.is_admin:
        local = scope.user_id == principal.user_id
        if local and scope.team_id:
            local = bool(
                role
                and role == principal.team_roles.get(scope.team_id)
                and (not write or role in {"owner", "admin"})
            )
        if not local and not (scope_reason or "").strip():
            raise ForbiddenError("Cross-scope effect access requires an audit reason")
        return
    if scope.user_id != principal.user_id:
        raise ForbiddenError("Effect scope mismatch")
    if scope.team_id and (
        not role
        or role != principal.team_roles.get(scope.team_id)
        or (write and role not in {"owner", "admin"})
    ):
        raise ForbiddenError("Effect resolution requires current team owner or admin")


async def authorize_effect_command(session, command, state):
    if command.command_type not in EFFECT_COMMANDS:
        return True
    payload = command.payload
    if command.command_type in {
        "PrepareEffectOperation",
        "StartEffectDispatch",
        "RecordEffectObservation",
    }:
        proof = command.activity_lease_proof
        if proof is None:
            return False
        spec = (
            payload.get("spec")
            if command.command_type == "PrepareEffectOperation"
            else next(
                (
                    item.spec.model_dump(mode="json")
                    for item in state.effect_operations
                    if str(item.spec.operation_id) == payload.get("operation_id")
                ),
                None,
            )
        )
        if (
            not spec
            or str(proof.activity_id) != str(spec["activity_id"])
            or proof.request_generation != spec["generation"]
        ):
            return False
        if command.command_type == "PrepareEffectOperation":
            existing = (
                (
                    await session.execute(
                        select(operations).where(
                            operations.c.operation_id == UUID(str(spec["operation_id"]))
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing and (
                str(existing["run_id"]) != command.stream_id
                or existing["owner_user_id"] != command.owner_user_id
                or existing["team_id"] != command.team_id
            ):
                return False
        if command.command_type == "StartEffectDispatch" and payload.get(
            "dispatch_proof"
        ) != proof.model_dump(mode="json"):
            return False
        task = (
            await session.execute(
                select(ExecutionActivityTaskORM)
                .where(ExecutionActivityTaskORM.activity_id == proof.activity_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        now = await session.scalar(select(func.clock_timestamp()))
        allowed = bool(
            task
            and task.aggregate_id == command.stream_id
            and task.owner_user_id == command.owner_user_id
            and task.team_id == command.team_id
            and task.request_generation == proof.request_generation
            and task.claim_generation == proof.claim_generation
            and task.claimed_by == proof.worker_id
            and task.status in {"claimed", "call_started"}
            and task.claim_deadline
            and task.claim_deadline > now
            and task.timeout_at > now
        )
        if allowed:
            _remember_effect_fence(session, command, min(task.claim_deadline, task.timeout_at))
        return allowed
    request_id = payload.get("request_id")
    if not request_id:
        return False
    row = (
        (
            await session.execute(
                select(requests).where(requests.c.request_id == UUID(request_id)).with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if (
        not row
        or row["status"] != "running"
        or row["claim_until"] is None
        or row["claim_until"] <= await session.scalar(select(func.clock_timestamp()))
        or row["command_type"] != command.command_type
        or row["command_payload_digest"] != digest(payload)
        or row["expected_revision"] != payload.get("expected_revision")
        or str(row["run_id"]) != command.stream_id
        or str(row["operation_id"]) != payload.get("operation_id")
        or row["owner_user_id"] != command.owner_user_id
        or row["team_id"] != command.team_id
    ):
        return False
    if row["principal"]:
        principal = Principal.model_validate(row["principal"])
        scope = (
            OwnerScope.team(principal.user_id, row["team_id"])
            if row["team_id"]
            else OwnerScope.personal(row["owner_user_id"])
        )
        try:
            await authorize_actor(session, scope, principal, row["scope_reason"])
        except ForbiddenError:
            return False
    elif row["kind"] not in {
        "auto_query",
        "local_not_sent",
        "reconcile_fact",
        "provider_observation",
    }:
        return False
    _remember_effect_fence(session, command, row["claim_until"])
    return True


async def project_effect_events(session, events, *, command=None):
    """Called by the orchestrator inside its event/Run/outbox transaction."""
    for event in events:
        if event.event_type not in EFFECT_EVENT_TYPES:
            continue
        if event.event_type in {"EffectOutcomeObserved", "EffectManualResolutionAccepted"}:
            continue
        operation_id = UUID(str(event.public_payload["operation_id"]))
        row = (
            (
                await session.execute(
                    select(operations)
                    .where(operations.c.operation_id == operation_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row and row["last_event_position"] >= event.position:
            continue
        if row and (
            row["owner_user_id"] != event.owner_user_id
            or row["team_id"] != event.team_id
            or str(row["run_id"]) != event.stream_id
        ):
            raise ValueError("effect identity belongs to a different stream scope")
        current = EffectState.model_validate(row["body"]) if row else None
        evolved = evolve_effect((current,) if current else (), event)[0]
        body = evolved.model_dump(mode="json")
        scope = {
            "owner_user_id": event.owner_user_id,
            "team_id": event.team_id,
            "scope_key": f"team:{event.team_id}"
            if event.team_id
            else f"user:{event.owner_user_id}",
        }
        values = dict(
            operation_id=operation_id,
            **scope,
            run_id=evolved.spec.run_id,
            invocation_id=evolved.spec.invocation_id,
            activity_id=evolved.spec.activity_id,
            provider_identity=evolved.spec.provider_identity,
            idempotency_key=evolved.spec.idempotency_key,
            body=body,
            revision=evolved.revision,
            last_event_position=event.position,
            updated_at=event.occurred_at,
        )
        await session.execute(
            insert(operations)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[operations.c.operation_id],
                set_={
                    key: value
                    for key, value in values.items()
                    if key
                    not in {
                        "operation_id",
                        "scope_key",
                        "owner_user_id",
                        "team_id",
                        "run_id",
                        "invocation_id",
                        "activity_id",
                    }
                },
            )
        )
        if event.public_payload.get("receipt_id"):
            await session.execute(
                insert(receipts)
                .values(
                    receipt_id=UUID(str(event.public_payload["receipt_id"])),
                    **scope,
                    operation_id=operation_id,
                    event_id=event.event_id,
                    event_position=event.position,
                    body=dict(event.internal_payload["observation"]),
                )
                .on_conflict_do_nothing(index_elements=[receipts.c.receipt_id])
            )
        if event.public_payload.get("request_id"):
            await session.execute(
                update(requests)
                .where(requests.c.request_id == UUID(str(event.public_payload["request_id"])))
                .values(status="completed", updated_at=func.now(), claim_id=None, claim_until=None)
            )
        if (
            command is not None
            and command.command_type == "RecordEffectObservation"
            and evolved.state in {"applied", "not_applied"}
        ):
            key = f"fact:{evolved.revision}"
            await session.execute(
                insert(requests)
                .values(
                    request_id=uuid5(NAMESPACE_URL, f"effect-fact:{operation_id}:{key}"),
                    **scope,
                    run_id=evolved.spec.run_id,
                    operation_id=operation_id,
                    kind="reconcile_fact",
                    idempotency_key=key,
                    fingerprint=digest({"revision": evolved.revision}),
                    expected_revision=evolved.revision,
                    principal=None,
                    body={},
                    status="pending",
                    due_at=func.now() + text("interval '5 seconds'"),
                )
                .on_conflict_do_nothing()
            )
        if not evolved.abandoned and (
            evolved.state == "prepared"
            or (
                evolved.state in {"sending", "pending", "unknown"}
                and evolved.spec.capability.value in {"dedupe_resolve", "resolve_only"}
            )
        ):
            key = f"auto:{evolved.revision}"
            query_id = uuid5(NAMESPACE_URL, f"effect-query:{operation_id}:{key}")
            await session.execute(
                insert(requests)
                .values(
                    request_id=query_id,
                    **scope,
                    run_id=evolved.spec.run_id,
                    operation_id=operation_id,
                    kind="local_not_sent" if evolved.state == "prepared" else "auto_query",
                    idempotency_key=key,
                    fingerprint=digest({"revision": evolved.revision}),
                    expected_revision=evolved.revision,
                    principal=None,
                    body={},
                    status="pending",
                    due_at=func.now() + text("interval '5 seconds'"),
                )
                .on_conflict_do_nothing()
            )


class PostgresEffectStore:
    def __init__(self, *, session_factory, authorization=None, orchestrator=None, uow_factory=None):
        self._sessions = session_factory
        self._authorization = authorization or AuthorizationContext.system("execution-kernel")
        self._orchestrator = orchestrator
        self._uow_factory = uow_factory

    def bind_orchestrator(self, orchestrator):
        self._orchestrator = orchestrator

    @asynccontextmanager
    async def transaction(self, *, scope=None, principal=None):
        async with self._sessions() as session:
            auth = (
                AuthorizationContext(mode=AuthorizationMode.USER, principal=principal, scope=scope)
                if principal
                else self._authorization
            )
            await configure_session_authorization(session, auth)
            yield session
            await session.commit()

    async def list(self, *, run_id, scope, principal, scope_reason=None):
        async with self.transaction(scope=scope, principal=principal) as session:
            await authorize_actor(session, scope, principal, scope_reason, write=False)
            rows = (
                (
                    await session.execute(
                        select(operations)
                        .where(
                            operations.c.run_id == run_id,
                            operations.c.scope_key == scope_values(scope)["scope_key"],
                        )
                        .order_by(operations.c.created_at, operations.c.operation_id)
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def get(
        self,
        *,
        operation_id,
        run_id=None,
        scope=None,
        principal=None,
        scope_reason=None,
        details=False,
    ):
        async with self.transaction(scope=scope, principal=principal) as session:
            if principal:
                await authorize_actor(session, scope, principal, scope_reason, write=False)
            conditions = [operations.c.operation_id == operation_id]
            if run_id:
                conditions.append(operations.c.run_id == run_id)
            if scope:
                conditions.append(operations.c.scope_key == scope_values(scope)["scope_key"])
            row = (await session.execute(select(operations).where(*conditions))).mappings().first()
            if not row:
                raise NotFoundError("External operation not found")
            result = dict(row)
            if details:
                result["receipts"] = [
                    dict(item["body"])
                    for item in (
                        await session.execute(
                            select(receipts.c.body)
                            .where(receipts.c.operation_id == operation_id)
                            .order_by(receipts.c.event_position)
                        )
                    ).mappings()
                ]
                result["query_history"] = [
                    self.public_request(item)
                    for item in (
                        await session.execute(
                            select(requests)
                            .where(requests.c.operation_id == operation_id)
                            .order_by(requests.c.created_at)
                        )
                    ).mappings()
                ]
            return result

    async def existing_request(
        self,
        *,
        kind,
        run_id,
        operation_id,
        scope,
        principal,
        scope_reason=None,
        expected_revision,
        idempotency_key,
        body,
    ):
        fingerprint = digest(
            {
                "kind": kind,
                "run_id": str(run_id),
                "expected_revision": expected_revision,
                "body": body,
                "actor": principal.user_id,
                "scope_reason": scope_reason,
            }
        )
        async with self.transaction(scope=scope, principal=principal) as session:
            await authorize_actor(session, scope, principal, scope_reason)
            row = (
                (
                    await session.execute(
                        select(requests).where(
                            requests.c.scope_key == scope_values(scope)["scope_key"],
                            requests.c.operation_id == operation_id,
                            requests.c.idempotency_key == idempotency_key,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            if row["run_id"] != run_id or row["fingerprint"] != fingerprint:
                raise ConflictError("Idempotency key has different effect intent")
            return self.public_request(row)

    async def submit_request(
        self,
        *,
        kind,
        run_id,
        operation_id,
        scope,
        principal,
        scope_reason,
        expected_revision,
        idempotency_key,
        body,
    ):
        fingerprint = digest(
            {
                "kind": kind,
                "run_id": str(run_id),
                "expected_revision": expected_revision,
                "body": body,
                "actor": principal.user_id,
                "scope_reason": scope_reason,
            }
        )
        async with self.transaction(scope=scope, principal=principal) as session:
            await authorize_actor(session, scope, principal, scope_reason)
            key = scope_values(scope)
            prior = (
                (
                    await session.execute(
                        select(requests).where(
                            requests.c.scope_key == key["scope_key"],
                            requests.c.operation_id == operation_id,
                            requests.c.idempotency_key == idempotency_key,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ConflictError("Idempotency key has different effect intent")
                return self.public_request(prior)
            op = (
                (
                    await session.execute(
                        select(operations).where(
                            operations.c.operation_id == operation_id,
                            operations.c.run_id == run_id,
                            operations.c.scope_key == key["scope_key"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not op:
                raise NotFoundError("External operation not found")
            if op["revision"] != expected_revision:
                raise ConflictError("Effect revision changed")
            request_id = uuid5(
                NAMESPACE_URL, f"effect-request:{key['scope_key']}:{operation_id}:{idempotency_key}"
            )
            row = (
                (
                    await session.execute(
                        insert(requests)
                        .values(
                            request_id=request_id,
                            **key,
                            run_id=run_id,
                            operation_id=operation_id,
                            kind=kind,
                            idempotency_key=idempotency_key,
                            fingerprint=fingerprint,
                            expected_revision=expected_revision,
                            principal=principal.model_dump(mode="json"),
                            scope_reason=scope_reason,
                            body=body,
                            status="pending",
                        )
                        .on_conflict_do_nothing()
                        .returning(requests)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                row = (
                    (
                        await session.execute(
                            select(requests).where(requests.c.request_id == request_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                if row["fingerprint"] != fingerprint:
                    raise ConflictError("Idempotency key has different effect intent")
            return self.public_request(row)

    async def get_request(
        self, *, run_id, operation_id, request_id, scope, principal, scope_reason=None
    ):
        async with self.transaction(scope=scope, principal=principal) as session:
            await authorize_actor(session, scope, principal, scope_reason, write=False)
            row = (
                (
                    await session.execute(
                        select(requests).where(
                            requests.c.run_id == run_id,
                            requests.c.operation_id == operation_id,
                            requests.c.request_id == request_id,
                            requests.c.scope_key == scope_values(scope)["scope_key"],
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise NotFoundError("Effect request not found")
            return self.public_request(row)

    @staticmethod
    def public_request(row):
        return {
            key: row[key]
            for key in (
                "request_id",
                "run_id",
                "operation_id",
                "status",
                "expected_revision",
                "error_code",
                "created_at",
                "updated_at",
            )
        }

    async def worker_command(self, command, *, preflight=None):
        if self._orchestrator is None:
            raise RuntimeError("API cannot execute effect commands")
        if preflight is not None:
            async with self._uow_factory() as uow:
                prepared = await self._orchestrator.prepare_command_transaction(
                    command, uow.db_session
                )
                await lock_scope(uow.db_session, command.owner_user_id, command.team_id)
                await preflight(uow)
                result = await self._orchestrator.handle_in_session(
                    command, uow.db_session, prepared=prepared
                )
                if result.status != "accepted":
                    raise ConflictError("Effect command rejected")
                await uow.db_session.flush()
                await ensure_effect_command_current(uow.db_session, command, if_issued=True)
                await uow.commit()
                return result
        result = await self._orchestrator.handle(command)
        if result.status != "accepted":
            raise ConflictError("Effect command rejected")
        return result

    async def claim_request(self):
        async with self.transaction() as session:
            row = (
                (
                    await session.execute(
                        select(requests)
                        .where(
                            or_(
                                requests.c.status == "pending",
                                and_(
                                    requests.c.status == "running",
                                    requests.c.claim_until < func.now(),
                                ),
                            ),
                            requests.c.due_at <= func.now(),
                            or_(
                                requests.c.kind.not_in(
                                    [
                                        "query",
                                        "auto_query",
                                        "local_not_sent",
                                        "reconcile_fact",
                                        "provider_observation",
                                    ]
                                ),
                                select(ExecutionActivityTaskORM.activity_id)
                                .where(
                                    ExecutionActivityTaskORM.activity_id
                                    == select(operations.c.activity_id)
                                    .where(operations.c.operation_id == requests.c.operation_id)
                                    .correlate(requests)
                                    .scalar_subquery(),
                                    ExecutionActivityTaskORM.status.in_(
                                        ["unknown", "cancelled", "failed", "succeeded"]
                                    ),
                                )
                                .exists(),
                            ),
                        )
                        .order_by(requests.c.due_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            values = {
                "status": "running",
                "claim_id": uuid4(),
                "claim_until": func.clock_timestamp() + text("interval '2 minutes'"),
                "attempts": row["attempts"] + 1,
                "updated_at": func.now(),
            }
            claimed = (
                (
                    await session.execute(
                        update(requests)
                        .where(requests.c.request_id == row["request_id"])
                        .values(**values)
                        .returning(requests)
                    )
                )
                .mappings()
                .one()
            )
            return dict(claimed)

    async def finish_request(self, row, *, error_code=None, command=None):
        async with self.transaction() as session:
            prepared = (
                await self._orchestrator.prepare_command_transaction(command, session)
                if command is not None and error_code is None
                else None
            )
            await lock_scope(session, row["owner_user_id"], row["team_id"])
            current = (
                (
                    await session.execute(
                        select(requests)
                        .where(
                            requests.c.request_id == row["request_id"],
                            requests.c.claim_id == row["claim_id"],
                            requests.c.status == "running",
                            requests.c.claim_until > func.clock_timestamp(),
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if not current or current["claim_until"] <= await session.scalar(
                select(func.clock_timestamp())
            ):
                return False
            if error_code:
                await session.execute(
                    update(requests)
                    .where(requests.c.request_id == row["request_id"])
                    .values(
                        status="paused" if error_code == "ForbiddenError" else "failed",
                        error_code=error_code,
                        updated_at=func.now(),
                        claim_id=None,
                        claim_until=None,
                    )
                )
                await session.flush()
                if current["claim_until"] <= await session.scalar(select(func.clock_timestamp())):
                    raise ActivityLeaseExpiredError(
                        "effect request authority expired before commit"
                    )
                return False
            if current["principal"]:
                principal = Principal.model_validate(current["principal"])
                scope = (
                    OwnerScope.team(principal.user_id, current["team_id"])
                    if current["team_id"]
                    else OwnerScope.personal(current["owner_user_id"])
                )
                await authorize_actor(session, scope, principal, current["scope_reason"])
            if current["kind"] == "provider_observation":
                revision = await session.scalar(
                    select(operations.c.revision).where(
                        operations.c.operation_id == current["operation_id"]
                    )
                )
                command = command.model_copy(
                    update={"payload": {**command.payload, "expected_revision": revision}}
                )
                await session.execute(
                    update(requests)
                    .where(requests.c.request_id == row["request_id"])
                    .values(expected_revision=revision)
                )
            await session.execute(
                update(requests)
                .where(requests.c.request_id == row["request_id"])
                .values(
                    command_type=command.command_type,
                    command_payload_digest=digest(command.payload),
                )
            )
            result = await self._orchestrator.handle_in_session(command, session, prepared=prepared)
            if result.status != "accepted":
                await session.execute(
                    update(requests)
                    .where(requests.c.request_id == row["request_id"])
                    .values(
                        status="failed",
                        error_code="EFFECT_REVISION_CONFLICT",
                        updated_at=func.now(),
                        claim_id=None,
                        claim_until=None,
                    )
                )
                return False
            # A duplicate receipt emits no new events and must still consume its request.
            await session.execute(
                update(requests)
                .where(requests.c.request_id == row["request_id"])
                .values(status="completed", updated_at=func.now(), claim_id=None, claim_until=None)
            )
            await session.flush()
            await ensure_effect_command_current(session, command)
            return True

    async def query_count(self, operation_id):
        async with self.transaction() as session:
            return await session.scalar(
                # A lost resolver response can lead to another claim of the
                # same request. Each claim may have sent a query, so conservatively
                # charge it even when no receipt survived the crashed worker.
                select(func.coalesce(func.sum(requests.c.attempts), 0))
                .select_from(requests)
                .where(
                    requests.c.operation_id == operation_id,
                    requests.c.kind.in_(["query", "auto_query"]),
                )
            )

    async def authorize_request(self, row):
        async with self.transaction() as session:
            if row["principal"]:
                principal = Principal.model_validate(row["principal"])
                scope = (
                    OwnerScope.team(principal.user_id, row["team_id"])
                    if row["team_id"]
                    else OwnerScope.personal(row["owner_user_id"])
                )
                await authorize_actor(session, scope, principal, row["scope_reason"])

    @staticmethod
    async def _verified_run_events(session, run_id, scope):
        from app.domain.execution.aggregate import replay
        from app.domain.execution.run import RunAggregate
        from app.infrastructure.execution.postgres_event_store import PostgresEventStore

        events = await PostgresEventStore(
            session, event_registries={"run": RunAggregate().event_registry}
        ).load_stream("run", str(run_id))
        if any(
            event.team_id != scope.team_id
            or event.owner_user_id != (None if scope.team_id else scope.user_id)
            for event in events
        ):
            raise ValueError("Effect recovery event scope mismatch")
        replay(RunAggregate(), events, stream_id=str(run_id))
        return events

    async def rebuild_scope(self, scope):
        """Replace derived indexes atomically using only this scope's verified facts."""
        async with self.transaction() as session:
            await lock_scope(session, None if scope.team_id else scope.user_id, scope.team_id)
            # Enumerate authoritative streams, never the potentially absent or
            # corrupt operation index that this recovery is repairing.
            stream_ids = await session.scalars(
                select(ExecutionEventORM.stream_id)
                .where(
                    ExecutionEventORM.stream_type == "run",
                    ExecutionEventORM.event_type == "EffectOperationPrepared",
                    ExecutionEventORM.team_id == scope.team_id
                    if scope.team_id
                    else and_(
                        ExecutionEventORM.owner_user_id == scope.user_id,
                        ExecutionEventORM.team_id.is_(None),
                    ),
                )
                .distinct()
                .order_by(ExecutionEventORM.stream_id)
            )
            key = scope_values(scope)["scope_key"]
            await session.execute(delete(receipts).where(receipts.c.scope_key == key))
            await session.execute(delete(operations).where(operations.c.scope_key == key))
            rebuilt = []
            for stream_id in stream_ids:
                events = await self._verified_run_events(session, UUID(stream_id), scope)
                await project_effect_events(session, events)
                rebuilt.append(UUID(stream_id))
            return tuple(rebuilt)

    async def rebuild_run(self, *, run_id, scope):
        """Repair indexes from hash-verified formal events while dispatch is fenced."""
        async with self.transaction() as session:
            await lock_scope(session, None if scope.team_id else scope.user_id, scope.team_id)
            events = await self._verified_run_events(session, run_id, scope)
            ids = select(operations.c.operation_id).where(
                operations.c.run_id == run_id,
                operations.c.scope_key == scope_values(scope)["scope_key"],
            )
            await session.execute(delete(receipts).where(receipts.c.operation_id.in_(ids)))
            await session.execute(
                delete(operations).where(
                    operations.c.run_id == run_id,
                    operations.c.scope_key == scope_values(scope)["scope_key"],
                )
            )
            await project_effect_events(session, events)

    async def projection_work(self, *, limit=20):
        async with self.transaction() as session:
            rows = (
                (
                    await session.execute(
                        select(operations)
                        .where(
                            operations.c.revision > operations.c.projected_revision,
                            operations.c.body["spec"]["provider_identity"].astext == "ops-actuator",
                        )
                        .order_by(operations.c.updated_at)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def mark_projected(self, operation_id, revision):
        async with self.transaction() as session:
            await session.execute(
                update(operations)
                .where(operations.c.operation_id == operation_id, operations.c.revision == revision)
                .values(projected_revision=revision)
            )

    async def defer_observation(self, spec, context, observation):
        """Retain a late transport observation for trusted verification, without events."""
        from app.domain.execution.commands import ActivityLeaseProof

        proof = ActivityLeaseProof(
            activity_id=spec.activity_id,
            request_generation=spec.generation,
            claim_generation=context.claim_generation,
            worker_id=context.worker_id,
        )
        async with self.transaction() as session:
            row = (
                (
                    await session.execute(
                        select(operations)
                        .where(operations.c.operation_id == spec.operation_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            current = EffectState.model_validate(row["body"])
            if current.spec != spec or current.dispatch_proof != proof:
                raise ConflictError("Late observation has no original send authority")
            body = {
                "observation": observation.model_dump(mode="json"),
                "dispatch_proof": proof.model_dump(mode="json"),
            }
            key = f"observation:{observation.receipt_id}"
            await session.execute(
                insert(requests)
                .values(
                    request_id=observation.receipt_id,
                    scope_key=row["scope_key"],
                    owner_user_id=row["owner_user_id"],
                    team_id=row["team_id"],
                    run_id=spec.run_id,
                    operation_id=spec.operation_id,
                    kind="provider_observation",
                    idempotency_key=key,
                    fingerprint=digest(body),
                    expected_revision=current.revision,
                    principal=None,
                    body=body,
                    status="pending",
                )
                .on_conflict_do_nothing()
            )
