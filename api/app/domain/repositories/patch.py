"""Typed patch-field sentinel shared by repository contracts."""
from typing import Final, final


@final
class UnsetType:
    """Marks a patch field as omitted, distinct from an explicit ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final[UnsetType] = UnsetType()
