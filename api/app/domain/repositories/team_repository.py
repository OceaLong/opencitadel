from abc import ABC, abstractmethod

from app.domain.models.team import Team, TeamMember, TeamRole


class TeamRepository(ABC):
    @abstractmethod
    async def get_by_id(self, team_id: str) -> Team | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[Team]: ...

    @abstractmethod
    async def list_all(self, limit: int = 100, offset: int = 0) -> list[Team]: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def count_members(self, team_id: str) -> int: ...

    @abstractmethod
    async def count_members_by_teams(self, team_ids: list[str]) -> dict[str, int]: ...

    @abstractmethod
    async def save(self, team: Team) -> None: ...

    @abstractmethod
    async def delete_by_id(self, team_id: str) -> None: ...

    @abstractmethod
    async def get_member(self, team_id: str, user_id: str) -> TeamMember | None: ...

    @abstractmethod
    async def list_members(self, team_id: str) -> list[TeamMember]: ...

    @abstractmethod
    async def add_member(self, member: TeamMember) -> None: ...

    @abstractmethod
    async def update_member_role(self, team_id: str, user_id: str, role: TeamRole) -> None: ...

    @abstractmethod
    async def remove_member(self, team_id: str, user_id: str) -> None: ...
