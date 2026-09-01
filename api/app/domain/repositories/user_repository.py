from abc import ABC, abstractmethod

from app.domain.models.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_id(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def list_by_ids(self, user_ids: list[str]) -> list[User]: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def get_by_username(self, username: str) -> User | None: ...

    @abstractmethod
    async def list(self, limit: int = 100, offset: int = 0) -> list[User]: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def count_by_role(self) -> dict[str, int]:
        """Count users grouped by global_role (e.g. {'admin': 1, 'user': 3})."""
        ...

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]: ...

    @abstractmethod
    async def delete_owned_resources(self, user_id: str) -> None: ...

    @abstractmethod
    async def transfer_personal_resources_to_team(self, user_id: str, team_id: str) -> int:
        """Reassign the user's personal resources (team_id IS NULL) to a team.

        Only rows the user owns individually (``owner_user_id = user_id`` and
        ``team_id IS NULL``) are moved; owner_user_id is preserved. Returns the
        number of rows reassigned. Must run under a system/admin authorization
        scope because it crosses the personal-ownership RLS predicate.
        """
        ...

    @abstractmethod
    async def revoke_security_material(self, user_id: str) -> None: ...

    @abstractmethod
    async def save(self, user: User) -> None: ...

    @abstractmethod
    async def delete_by_id(self, user_id: str) -> None: ...
