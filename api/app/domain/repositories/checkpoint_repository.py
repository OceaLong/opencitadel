#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import List, Optional, Protocol

from app.domain.models.checkpoint import Checkpoint


class CheckpointRepository(Protocol):
    """Checkpoint repository protocol."""

    async def save(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint."""
        ...

    async def get_by_id(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Get checkpoint by id."""
        ...

    async def list_by_session(self, session_id: str) -> List[Checkpoint]:
        """List checkpoints for a session ordered by creation time."""
        ...

    async def delete_from(self, session_id: str, from_created_at: datetime, inclusive: bool = True) -> None:
        """Delete checkpoints from the given timestamp onward."""
        ...

    async def delete_by_session(self, session_id: str) -> None:
        """Delete all checkpoints for a session."""
        ...
