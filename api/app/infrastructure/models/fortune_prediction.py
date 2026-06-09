#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models.fortune_prediction import FortunePrediction
from .base import Base


class FortunePredictionModel(Base):
    __tablename__ = "marketplace_fortune_predictions"
    __table_args__ = (
        Index("ix_fortune_predictions_share_id", "share_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    share_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    input_profile: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP(0)")
    )

    def to_domain(self) -> FortunePrediction:
        return FortunePrediction(
            id=self.id,
            share_id=self.share_id,
            mode=self.mode,
            question=self.question,
            input_profile=self.input_profile or {},
            result=self.result or {},
            created_at=self.created_at,
        )

    @classmethod
    def from_domain(cls, prediction: FortunePrediction) -> "FortunePredictionModel":
        return cls(
            id=prediction.id,
            share_id=prediction.share_id,
            mode=prediction.mode,
            question=prediction.question,
            input_profile=prediction.input_profile,
            result=prediction.result,
            created_at=prediction.created_at,
        )
