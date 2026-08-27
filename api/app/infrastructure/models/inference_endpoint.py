from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.inference import (
    InferenceEndpoint,
    InferenceProvider,
    ResourceVisibility,
)
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption

from .base import Base


class InferenceEndpointORM(Base):
    __tablename__ = "inference_endpoints"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    credential: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    credential_encryption: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'fernet_v2'"),
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    team_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'global'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(0)"),
    )

    @classmethod
    def from_domain(
        cls,
        endpoint: InferenceEndpoint,
        encrypted_credential: str,
    ) -> "InferenceEndpointORM":
        return cls(
            id=endpoint.id,
            display_name=endpoint.display_name,
            provider=endpoint.provider.value,
            base_url=endpoint.base_url,
            credential=encrypted_credential,
            credential_encryption=ApiKeyEncryption.FERNET_V2,
            owner_user_id=endpoint.owner_user_id,
            team_id=endpoint.team_id,
            visibility=endpoint.visibility.value,
        )

    def to_domain(self, decrypted_credential: str) -> InferenceEndpoint:
        return InferenceEndpoint(
            id=self.id,
            display_name=self.display_name,
            provider=InferenceProvider(self.provider),
            base_url=self.base_url,
            credential=decrypted_credential,
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
