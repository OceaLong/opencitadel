"""Fail-closed registry for explicitly admitted production Activities."""

from app.domain.execution.activity import ActivityHandler


class UnknownActivityTypeError(LookupError):
    pass


class ActivityRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], ActivityHandler] = {}

    def register(self, handler: ActivityHandler, *, handler_version: str = "1") -> None:
        activity_type = handler.activity_type.strip()
        if not activity_type:
            raise ValueError("activity_type must not be empty")
        version = str(handler_version).strip()
        if not version:
            raise ValueError("handler_version must not be empty")
        key = (activity_type, version)
        if key in self._handlers:
            raise ValueError(f"duplicate Activity handler: {activity_type}/{version}")
        self._handlers[key] = handler

    def resolve(self, activity_type: str, *, handler_version: str = "1") -> ActivityHandler:
        normalized = activity_type.strip()
        try:
            return self._handlers[(normalized, str(handler_version))]
        except KeyError as error:
            raise UnknownActivityTypeError(
                f"no admitted Activity handler for {normalized}/{handler_version}"
            ) from error

    @property
    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted({name for name, _ in self._handlers}))

    @property
    def registered_implementations(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._handlers))


def create_activity_registry(*handlers: ActivityHandler) -> ActivityRegistry:
    registry = ActivityRegistry()
    for handler in handlers:
        registry.register(handler)
    return registry


__all__ = [
    "ActivityRegistry",
    "UnknownActivityTypeError",
    "create_activity_registry",
]
