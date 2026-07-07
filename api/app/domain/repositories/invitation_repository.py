#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.invitation import Invitation, InvitationType


class InvitationRepository(ABC):
    @abstractmethod
    async def get_by_token(self, token: str) -> Optional[Invitation]:
        ...

    @abstractmethod
    async def get_pending_team_invitation(self, team_id: str, email: str) -> Optional[Invitation]:
        ...

    @abstractmethod
    async def list(self, invitation_type: InvitationType | None = None, limit: int = 100, offset: int = 0) -> List[Invitation]:
        ...

    @abstractmethod
    async def count(self, invitation_type: InvitationType | None = None) -> int:
        ...

    @abstractmethod
    async def save(self, invitation: Invitation) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, invitation_id: str) -> None:
        ...
