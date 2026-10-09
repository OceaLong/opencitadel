import builtins
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.audit_log import AuditLog
from app.domain.repositories.audit_repository import AuditRepository
from app.domain.services.audit_chain import (
    GENESIS,
    compute_entry_hash,
    entry_fields,
    shard_key_for,
)
from app.infrastructure.models.audit_log import AuditLogORM


class DBAuditRepository(AuditRepository):
    def __init__(
        self,
        db_session: AsyncSession,
        *,
        signing_key: str,
        signing_key_id: str,
    ) -> None:
        if not signing_key:
            raise ValueError("audit signing key must not be empty")
        if not signing_key_id.strip():
            raise ValueError("audit signing key id must not be empty")
        self.db_session = db_session
        self._signing_key = signing_key
        self._signing_key_id = signing_key_id

    async def add(self, log: AuditLog) -> None:
        shard_key = shard_key_for(team_id=log.team_id, actor_user_id=log.actor_user_id)
        # Per-shard transactional advisory lock: writers to different shards
        # (distinct teams/users/system) take different locks and never queue on
        # each other, removing the platform-wide serialization bottleneck.
        # Writers to the *same* shard still serialize, which is what keeps that
        # shard's chain_seq gap-free and its prev_hash links well-ordered.
        # hashtext() is computed in-DB so the lock key matches across replicas.
        await self.db_session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:shard_key))"),
            {"shard_key": shard_key},
        )
        last_stmt = (
            select(AuditLogORM.chain_seq, AuditLogORM.entry_hash)
            .where(AuditLogORM.shard_key == shard_key)
            .where(AuditLogORM.chain_seq.isnot(None))
            .order_by(AuditLogORM.chain_seq.desc())
            .limit(1)
        )
        result = await self.db_session.execute(last_stmt)
        last = result.first()
        next_seq = (last.chain_seq if last and last.chain_seq else 0) + 1
        prev_hash = last.entry_hash if last and last.entry_hash else GENESIS

        fields = entry_fields(
            chain_seq=next_seq,
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_ip=log.actor_ip,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            team_id=log.team_id,
            session_id=log.session_id,
            request_id=log.request_id,
            metadata=log.metadata,
            created_at=log.created_at,
        )
        entry_hash = compute_entry_hash(self._signing_key, fields, prev_hash)
        log.chain_seq = next_seq
        log.signing_key_id = self._signing_key_id
        log.prev_hash = prev_hash
        log.entry_hash = entry_hash
        self.db_session.add(AuditLogORM.from_domain(log))

    async def add_evaluation(self, log: AuditLog, *, authorization) -> None:
        """Closed same-transaction bridge; never exposes a privileged callback."""
        from app.domain.models.authorization import AuthorizationContext
        from app.infrastructure.security.db_authorization import configure_session_authorization

        allowed = {
            "create",
            "import_validate",
            "import_apply",
            "update_case",
            "from_run",
            "publish",
        }
        configuration_actions = {
            "evaluation.config.create": ("config", "create"),
            "evaluation.config.update": ("config", "update"),
            "evaluation.config.delete": ("config", "delete"),
            "evaluation.config.publish": ("config", "publish"),
            "evaluation.rubric.create": ("rubric", "create"),
            "evaluation.rubric.update": ("rubric", "update"),
            "evaluation.rubric.delete": ("rubric", "delete"),
            "evaluation.rubric.publish": ("rubric", "publish"),
            "evaluation.suite.create": ("suite", "create"),
            "evaluation.suite.update": ("suite", "update"),
            "evaluation.suite.delete": ("suite", "delete"),
            "evaluation.suite.publish": ("suite", "publish"),
        }
        environment = log.action in {
            "evaluation.environment.register",
            "evaluation.environment.repair",
        }
        recording = log.action in {"evaluation.recording.create", "evaluation.recording.publish"}
        configuration = configuration_actions.get(log.action)
        operation = (
            (configuration[0] + "." + configuration[1])
            if configuration
            else (
                log.action.removeprefix("evaluation.")
                if recording or environment
                else log.action.removeprefix("evaluation.dataset.")
            )
        )
        principal, scope = authorization.principal, authorization.scope
        if (
            (
                configuration is None
                and not recording
                and not environment
                and (operation not in allowed or log.action != "evaluation.dataset." + operation)
            )
            or principal is None
            or scope is None
            or principal.is_auditor
            or log.actor_user_id != principal.user_id
            or scope.user_id != principal.user_id
            or log.team_id != scope.team_id
            or log.request_id != authorization.request_id
            or log.resource_type
            != (
                "evaluation_environment"
                if environment
                else "evaluation_recording"
                if recording
                else ("evaluation_" + configuration[0] if configuration else "evaluation_dataset")
            )
            or log.session_id is not None
            or log.actor_ip
            or set(log.metadata) != {"revision"}
            or type(log.metadata["revision"]) is not int
        ):
            raise PermissionError("invalid evaluation audit intent")
        # Flush ordinary writes under original authority before the narrow bridge.
        await self.db_session.flush()
        scope_key = "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id
        receipt = (
            (
                await self.db_session.execute(
                    text(
                        "SELECT created_by,result FROM evaluation_mutations WHERE scope_key=:scope AND request_id=:request"
                    ),
                    {"scope": scope_key, "request": log.request_id},
                )
            )
            .mappings()
            .first()
        )
        if (
            not receipt
            or receipt["created_by"] != principal.user_id
            or receipt["result"].get("operation") != operation
            or receipt["result"].get("audit_resource_id") != log.resource_id
            or receipt["result"].get("revision") != log.metadata["revision"]
        ):
            raise PermissionError("evaluation audit receipt mismatch")
        if environment:
            if not principal.is_admin:
                raise PermissionError("invalid environment audit intent")
            if operation == "environment.register":
                if receipt["result"].get("kind") not in {"environment", "target", "credential"}:
                    raise PermissionError("invalid environment audit intent")
                exists = await self.db_session.scalar(
                    text(
                        "SELECT 1 FROM evaluation_environment_registry WHERE scope_key=:scope AND id::text=:id AND kind=:kind AND revision=:revision"
                    ),
                    {
                        "scope": scope_key,
                        "id": log.resource_id,
                        "kind": receipt["result"]["kind"],
                        "revision": log.metadata["revision"],
                    },
                )
            else:
                exists = await self.db_session.scalar(
                    text(
                        "SELECT 1 FROM evaluation_environment_repairs WHERE scope_key=:scope AND id::text=:id AND lease_revision=:revision AND created_by=:actor"
                    ),
                    {
                        "scope": scope_key,
                        "id": log.resource_id,
                        "revision": log.metadata["revision"],
                        "actor": principal.user_id,
                    },
                )
            if not exists:
                raise PermissionError("environment audit registry mismatch")
        elif recording:
            row = (
                (
                    await self.db_session.execute(
                        text(
                            "SELECT revision,status,result_version FROM evaluation_recording_jobs WHERE scope_key=:scope AND id::text=:id"
                        ),
                        {"scope": scope_key, "id": log.resource_id},
                    )
                )
                .mappings()
                .first()
            )
            if (
                not row
                or row["revision"] != log.metadata["revision"]
                or (
                    operation == "recording.publish"
                    and (
                        row["status"] != "ready"
                        or str(row["result_version"]) != receipt["result"].get("result_version")
                    )
                )
            ):
                raise PermissionError("evaluation audit recording mismatch")
        elif configuration:
            kind, mutation = configuration
            row = (
                (
                    await self.db_session.execute(
                        text(
                            "SELECT revision,deleted FROM evaluation_configuration_drafts WHERE scope_key=:scope AND id::text=:id AND kind=:kind"
                        ),
                        {"scope": scope_key, "id": log.resource_id, "kind": kind},
                    )
                )
                .mappings()
                .first()
            )
            if (
                not row
                or row["revision"] != log.metadata["revision"]
                or row["deleted"] != (mutation == "delete")
            ):
                raise PermissionError("evaluation audit configuration mismatch")
            if mutation == "publish":
                version = await self.db_session.scalar(
                    text(
                        f"SELECT 1 FROM evaluation_{kind}_versions WHERE scope_key=:scope AND id::text=:id AND entity_id::text=:entity AND revision=:revision"
                    ),
                    {
                        "scope": scope_key,
                        "id": receipt["result"].get("id"),
                        "entity": log.resource_id,
                        "revision": log.metadata["revision"],
                    },
                )
                if not version:
                    raise PermissionError("evaluation audit version mismatch")
        else:
            exists = await self.db_session.scalar(
                text("SELECT 1 FROM evaluation_datasets WHERE scope_key=:scope AND id::text=:id"),
                {"scope": scope_key, "id": log.resource_id},
            )
            if not exists:
                raise PermissionError("evaluation audit dataset unavailable")
            if operation == "publish":
                version = await self.db_session.scalar(
                    text(
                        "SELECT 1 FROM evaluation_dataset_versions WHERE scope_key=:scope AND id::text=:id AND dataset_id::text=:dataset AND revision=:revision"
                    ),
                    {
                        "scope": scope_key,
                        "id": receipt["result"].get("id"),
                        "dataset": log.resource_id,
                        "revision": log.metadata["revision"],
                    },
                )
                if not version:
                    raise PermissionError("evaluation audit version mismatch")
        try:
            await configure_session_authorization(
                self.db_session, AuthorizationContext.system("evaluation-audit-append")
            )
            await self.add(log)
            await self.db_session.flush()
        finally:
            # Restoration failure propagates: caller UoW must roll back, never commit.
            await configure_session_authorization(self.db_session, authorization)

    async def add_archive(self, log: AuditLog, *, authorization) -> None:
        from app.domain.models.authorization import AuthorizationContext
        from app.infrastructure.security.db_authorization import configure_session_authorization

        principal, scope = authorization.principal, authorization.scope
        if (
            principal is None
            or scope is None
            or principal.is_auditor
            or log.action != "evaluation.archive"
            or log.actor_user_id != principal.user_id
            or log.team_id != scope.team_id
            or log.request_id != authorization.request_id
            or log.session_id is not None
            or log.actor_ip
            or set(log.metadata) != {"revision"}
        ):
            raise PermissionError("invalid archive audit intent")
        row = (
            (
                await self.db_session.execute(
                    text(
                        "SELECT kind,resource_id,revision,created_by FROM evaluation_resource_archives WHERE scope_key=:scope AND request_id=:request"
                    ),
                    {
                        "scope": "team:" + scope.team_id
                        if scope.team_id
                        else "user:" + scope.user_id,
                        "request": log.request_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["created_by"] != principal.user_id
            or str(row["resource_id"]) != log.resource_id
            or log.resource_type != "evaluation_" + row["kind"]
            or log.metadata["revision"] != row["revision"]
        ):
            raise PermissionError("archive audit receipt mismatch")
        try:
            await configure_session_authorization(
                self.db_session, AuthorizationContext.system("evaluation-audit-append")
            )
            await self.add(log)
            await self.db_session.flush()
        finally:
            await configure_session_authorization(self.db_session, authorization)

    async def add_review(self, log: AuditLog, *, authorization) -> None:
        """Closed command-receipt bridge, retaining the caller transaction/actor."""
        from app.domain.models.authorization import AuthorizationContext
        from app.infrastructure.security.db_authorization import configure_session_authorization

        principal, scope = authorization.principal, authorization.scope
        if (
            principal is None
            or scope is None
            or principal.is_auditor
            or log.action
            not in {
                "evaluation.review.human",
                "evaluation.review.rescore",
                "evaluation.review.cancel",
            }
            or log.actor_user_id != principal.user_id
            or scope.user_id != principal.user_id
            or log.team_id != scope.team_id
            or log.request_id != authorization.request_id
            or log.resource_type != "evaluation_result"
            or log.session_id is not None
            or log.actor_ip
            or set(log.metadata) != {"command_id", "evaluation_revision", "result_revision"}
        ):
            raise PermissionError("invalid review audit intent")
        await self.db_session.flush()
        scope_key = "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id
        row = (
            (
                await self.db_session.execute(
                    text(
                        "SELECT * FROM evaluation_review_commands WHERE scope_key=:scope AND id::text=:id"
                    ),
                    {"scope": scope_key, "id": log.metadata["command_id"]},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            not row
            or row["created_by"] != principal.user_id
            or str(row["result_id"]) != log.resource_id
            or row["request_id"] != log.request_id
            or "evaluation.review." + row["kind"] != log.action
            or any(
                row["receipt"][key] != log.metadata[key]
                for key in ("evaluation_revision", "result_revision")
            )
        ):
            raise PermissionError("review audit receipt mismatch")
        try:
            await configure_session_authorization(
                self.db_session, AuthorizationContext.system("evaluation-audit-append")
            )
            await self.add(log)
            await self.db_session.flush()
        finally:
            await configure_session_authorization(self.db_session, authorization)

    async def add_batch(self, log: AuditLog, *, authorization) -> None:
        from app.domain.models.authorization import AuthorizationContext
        from app.infrastructure.security.db_authorization import configure_session_authorization

        principal, scope = authorization.principal, authorization.scope
        if (
            principal is None
            or scope is None
            or principal.is_auditor
            or log.action
            not in {
                "evaluation.batch.start",
                "evaluation.batch.cancel",
                "evaluation.batch.retry_failed",
            }
            or log.actor_user_id != principal.user_id
            or scope.user_id != principal.user_id
            or log.team_id != scope.team_id
            or log.request_id != authorization.request_id
            or log.resource_type != "evaluation_batch"
            or log.session_id is not None
            or log.actor_ip
            or set(log.metadata) != {"command_id"}
        ):
            raise PermissionError("invalid batch audit intent")
        await self.db_session.flush()
        scope_key = "team:" + scope.team_id if scope.team_id else "user:" + scope.user_id
        row = (
            (
                await self.db_session.execute(
                    text(
                        "SELECT * FROM evaluation_batch_commands WHERE scope_key=:scope AND id::text=:id"
                    ),
                    {"scope": scope_key, "id": log.metadata["command_id"]},
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            not row
            or row["created_by"] != principal.user_id
            or str(row["batch_id"]) != log.resource_id
            or row["request_id"] != log.request_id
            or "evaluation.batch." + row["kind"] != log.action
        ):
            raise PermissionError("batch audit receipt mismatch")
        try:
            await configure_session_authorization(
                self.db_session, AuthorizationContext.system("evaluation-audit-append")
            )
            await self.add(log)
            await self.db_session.flush()
        finally:
            await configure_session_authorization(self.db_session, authorization)

    async def list(
        self,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        stmt = select(AuditLogORM)
        if actor_user_id:
            stmt = stmt.where(AuditLogORM.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditLogORM.action == action)
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if resource_type:
            stmt = stmt.where(AuditLogORM.resource_type == resource_type)
        if session_id:
            stmt = stmt.where(AuditLogORM.session_id == session_id)
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        stmt = (
            stmt.order_by(AuditLogORM.created_at.desc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 1000)))
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def get_by_id(self, log_id: str) -> AuditLog | None:
        stmt = select(AuditLogORM).where(AuditLogORM.id == log_id)
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_chained(
        self,
        *,
        limit: int | None = None,
        resource_id: str | None = None,
        session_id: str | None = None,
    ) -> builtins.list[AuditLog]:
        stmt = select(AuditLogORM).where(AuditLogORM.chain_seq.isnot(None))
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if session_id:
            stmt = stmt.where(AuditLogORM.session_id == session_id)
        # Group each shard's rows contiguously and ascending by chain_seq so the
        # per-shard verification walk (verify_chain_logs) sees every shard's
        # chain in order. chain_seq is only monotonic within a shard now.
        stmt = stmt.order_by(AuditLogORM.shard_key.asc(), AuditLogORM.chain_seq.asc())
        if limit is not None:
            stmt = stmt.limit(max(1, limit))
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(
        self,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        resource_id: str | None = None,
        resource_type: str | None = None,
        session_id: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(AuditLogORM)
        if actor_user_id:
            stmt = stmt.where(AuditLogORM.actor_user_id == actor_user_id)
        if action:
            stmt = stmt.where(AuditLogORM.action == action)
        if resource_id:
            stmt = stmt.where(AuditLogORM.resource_id == resource_id)
        if resource_type:
            stmt = stmt.where(AuditLogORM.resource_type == resource_type)
        if session_id:
            stmt = stmt.where(AuditLogORM.session_id == session_id)
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_by_actions(
        self,
        actions: builtins.list[str],
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        if not actions:
            return 0
        stmt = select(func.count()).select_from(AuditLogORM).where(AuditLogORM.action.in_(actions))
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_by_action_prefix(
        self,
        prefix: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        if not prefix:
            return 0
        stmt = (
            select(func.count())
            .select_from(AuditLogORM)
            .where(AuditLogORM.action.like(f"{prefix}%"))
        )
        if start_at:
            stmt = stmt.where(AuditLogORM.created_at >= start_at)
        if end_at:
            stmt = stmt.where(AuditLogORM.created_at <= end_at)
        result = await self.db_session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_recent_chained(self, limit: int = 20) -> builtins.list[AuditLog]:
        # Same ordering key as the tail lookup in add() above (chain_seq
        # desc) -- chain_seq is the tamper-evident write-order sequence,
        # not created_at, so this sample is fit for checking whether
        # created_at tracks chain order rather than trivially agreeing with
        # itself. DESC + limit at the DB level (index-friendly), then
        # reverse in Python to hand callers ascending chain order.
        stmt = (
            select(AuditLogORM)
            .where(AuditLogORM.chain_seq.isnot(None))
            .order_by(AuditLogORM.chain_seq.desc())
            .limit(max(1, limit))
        )
        result = await self.db_session.execute(stmt)
        records = list(reversed(result.scalars().all()))
        return [record.to_domain() for record in records]

    async def daily_action_counts(
        self,
        actions: builtins.list[str],
        *,
        since: datetime | None = None,
    ) -> builtins.list[dict[str, Any]]:
        if not actions:
            return []
        date_col = func.date(AuditLogORM.created_at)
        stmt = select(date_col.label("date"), AuditLogORM.action, func.count()).where(
            AuditLogORM.action.in_(actions)
        )
        if since is not None:
            stmt = stmt.where(AuditLogORM.created_at >= since)
        stmt = stmt.group_by(date_col, AuditLogORM.action).order_by(date_col.asc())
        result = await self.db_session.execute(stmt)
        return [
            {"date": str(date_value), "action": action, "count": int(count)}
            for date_value, action, count in result.all()
        ]
