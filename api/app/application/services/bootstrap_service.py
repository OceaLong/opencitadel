import logging
from collections.abc import Callable

from app.application.ports.crypto import BootstrapAdminCredentials, PasswordHashPort
from app.application.services.skill_service import SkillService
from app.domain.models.user import GlobalRole, User
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


async def bootstrap_data(
    uow_factory: Callable[[], IUnitOfWork],
    skill_service: SkillService,
    credentials: BootstrapAdminCredentials,
    password_hasher: PasswordHashPort,
) -> None:
    """Seed mandatory built-ins and the first administrator.

    Startup fails if either operation fails so the API never serves from a
    partially initialized database.
    """
    await skill_service.seed_builtin_skills()
    await bootstrap_admin_user(uow_factory, credentials, password_hasher)


async def bootstrap_admin_user(
    uow_factory: Callable[[], IUnitOfWork],
    credentials: BootstrapAdminCredentials,
    password_hasher: PasswordHashPort,
) -> None:
    email = credentials.email.strip().lower()
    if not email:
        return
    password = credentials.password.strip()
    if not password:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_PASSWORD is required when BOOTSTRAP_ADMIN_EMAIL is configured"
        )
    async with uow_factory() as uow:
        existing = await uow.user.get_by_email(email)
        if existing:
            return

        users = await uow.user.list(limit=1)
        if users:
            return

        user = User(
            email=email,
            username=email.split("@", 1)[0] or "admin",
            password_hash=password_hasher.hash(password),
            display_name="Administrator",
            global_role=GlobalRole.ADMIN,
        )
        await uow.user.save(user)
        await uow.commit()
        logger.info("Bootstrap admin user created: %s", email)
