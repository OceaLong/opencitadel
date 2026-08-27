from abc import ABC, abstractmethod

from app.domain.models.oauth_identity import OAuthIdentity


class OAuthIdentityRepository(ABC):
    @abstractmethod
    async def get_by_provider_identity(
        self, provider: str, provider_user_id: str
    ) -> OAuthIdentity | None: ...

    @abstractmethod
    async def save(self, identity: OAuthIdentity) -> None: ...
