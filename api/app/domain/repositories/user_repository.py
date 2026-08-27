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
    async def revoke_security_material(self, user_id: str) -> None: ...

    @abstractmethod
    async def save(self, user: User) -> None: ...

    @abstractmethod
    async def delete_by_id(self, user_id: str) -> None: ...
