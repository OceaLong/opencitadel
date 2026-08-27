"""Domain port for provider-backed image generation."""

from __future__ import annotations

from typing import Protocol

from app.domain.external.file_storage import FileStorage
from app.domain.models.inference import ResolvedInferenceModel


class ImageGenerator(Protocol):
    async def generate(
        self,
        prompt: str,
        model: ResolvedInferenceModel,
        file_storage: FileStorage,
        *,
        size: str,
        quality: str,
        owner_user_id: str | None,
        team_id: str | None,
    ) -> str | None:
        """Generate, persist, and return a governed image reference."""
        ...


__all__ = ["ImageGenerator"]
