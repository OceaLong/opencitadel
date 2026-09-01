from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.inference import (
    ChatModelSettings,
    EmbeddingModelSettings,
    InferenceCapabilities,
    InferenceModel,
    InferenceModelKind,
    ResourceVisibility,
)

from .base import Base


class InferenceModelORM(Base):
    __tablename__ = "inference_models"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("inference_endpoints.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    input_price_per_million: Mapped[float] = mapped_column(
        nullable=False,
        server_default=text("0"),
    )
    output_price_per_million: Mapped[float] = mapped_column(
        nullable=False,
        server_default=text("0"),
    )
    extra_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
    def from_domain(cls, model: InferenceModel) -> "InferenceModelORM":
        return cls(
            id=model.id,
            endpoint_id=model.endpoint_id,
            display_name=model.display_name,
            model_name=model.model_name,
            kind=model.kind.value,
            settings=model.settings.model_dump(mode="json"),
            input_price_per_million=model.input_price_per_million,
            output_price_per_million=model.output_price_per_million,
            extra_params=model.extra_params,
            capabilities=model.capabilities.model_dump(mode="json"),
            owner_user_id=model.owner_user_id,
            team_id=model.team_id,
            visibility=model.visibility.value,
        )

    def to_domain(self) -> InferenceModel:
        kind = InferenceModelKind(self.kind)
        settings_type = (
            ChatModelSettings if kind == InferenceModelKind.CHAT else EmbeddingModelSettings
        )
        return InferenceModel(
            id=self.id,
            endpoint_id=self.endpoint_id,
            display_name=self.display_name,
            model_name=self.model_name,
            kind=kind,
            settings=settings_type.model_validate(self.settings),
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            extra_params=self.extra_params or {},
            capabilities=InferenceCapabilities.model_validate(self.capabilities or {}),
            owner_user_id=self.owner_user_id,
            team_id=self.team_id,
            visibility=ResourceVisibility(self.visibility),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
