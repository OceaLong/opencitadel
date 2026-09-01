import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.audit_log import AuditLog
from app.domain.services.audit_chain import shard_key_for

from .base import Base


class AuditLogORM(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # The hash chain is partitioned by shard_key (per team / per user /
        # system); chain_seq is monotonic *within* a shard, so uniqueness is on
        # (shard_key, chain_seq) rather than chain_seq alone -- otherwise two
        # shards would collide at chain_seq=1. NULL chain_seq (unchained rows)
        # is exempt via the partial predicate. This btree also backs the
        # ``max(chain_seq) WHERE shard_key = :k`` tail lookup in add().
        Index(
            "uq_audit_logs_shard_chain_seq",
            "shard_key",
            "chain_seq",
            unique=True,
            postgresql_where=text("chain_seq IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    actor_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_ip: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("''"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("''")
    )
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    team_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Hash-chain shard: derived from (team_id, actor_user_id) at write time (see
    # shard_key_for). Persisted so the per-shard advisory lock, the per-shard
    # max(chain_seq) tail lookup, and the (shard_key, chain_seq) uniqueness
    # constraint can all key off a single indexed column.
    shard_key: Mapped[str] = mapped_column(
        String(320), nullable=False, server_default=text("'system'")
    )
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default=text("''"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    chain_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    signing_key_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    @classmethod
    def from_domain(cls, log: AuditLog) -> "AuditLogORM":
        return cls(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_ip=log.actor_ip,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            team_id=log.team_id,
            shard_key=shard_key_for(team_id=log.team_id, actor_user_id=log.actor_user_id),
            session_id=log.session_id,
            request_id=log.request_id,
            metadata_json=log.metadata,
            chain_seq=log.chain_seq,
            signing_key_id=log.signing_key_id,
            prev_hash=log.prev_hash or None,
            entry_hash=log.entry_hash or None,
            created_at=log.created_at,
        )

    def to_domain(self) -> AuditLog:
        return AuditLog(
            id=self.id,
            actor_user_id=self.actor_user_id,
            actor_ip=self.actor_ip,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            team_id=self.team_id,
            session_id=self.session_id,
            request_id=self.request_id,
            metadata=self.metadata_json or {},
            created_at=self.created_at,
            chain_seq=self.chain_seq,
            signing_key_id=self.signing_key_id,
            prev_hash=self.prev_hash or "",
            entry_hash=self.entry_hash or "",
        )
