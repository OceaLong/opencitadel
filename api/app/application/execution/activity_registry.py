"""Fail-closed registry for explicitly admitted production Activities."""

from app.domain.execution.activity import ActivityHandler


class UnknownActivityTypeError(LookupError):
    pass


class ActivityRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ActivityHandler] = {}

    def register(self, handler: ActivityHandler) -> None:
        activity_type = handler.activity_type.strip()
        if not activity_type:
            raise ValueError("activity_type must not be empty")
        if activity_type in self._handlers:
            raise ValueError(f"duplicate Activity handler: {activity_type}")
        self._handlers[activity_type] = handler

    def resolve(self, activity_type: str) -> ActivityHandler:
        normalized = activity_type.strip()
        try:
            return self._handlers[normalized]
        except KeyError as error:
            raise UnknownActivityTypeError(
                f"no admitted Activity handler for {normalized}"
            ) from error

    @property
    def registered_types(self) -> tuple[str, ...]:
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
