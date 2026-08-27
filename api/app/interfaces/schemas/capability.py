from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.application.services.capability_service import CapabilityStateValue


class CapabilityStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CapabilityStateValue
    reason_key: str | None = None
    model_id: str | None = None
    details: dict[str, Any]


class CapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    items: dict[str, CapabilityStateResponse]
