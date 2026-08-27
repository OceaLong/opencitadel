from contextvars import ContextVar

from app.domain.models.scope import Principal

current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


def get_principal() -> Principal | None:
    return current_principal.get()


def set_principal(principal: Principal | None):
    return current_principal.set(principal)
