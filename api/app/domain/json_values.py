"""Shared strict and immutable JSON value primitives for domain contracts."""

from __future__ import annotations

import math
from copy import deepcopy

from pydantic import JsonValue
from pydantic_core import PydanticCustomError


def _immutable(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise TypeError("domain JSON values are immutable")


class FrozenJsonDict(dict[str, JsonValue]):
    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, memo: dict[int, object]) -> dict[str, object]:
        return {deepcopy(key, memo): deepcopy(value, memo) for key, value in self.items()}


class FrozenJsonList(list[JsonValue]):
    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __deepcopy__(self, memo: dict[int, object]) -> list[object]:
        return [deepcopy(value, memo) for value in self]


def deep_freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return FrozenJsonDict({key: deep_freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenJsonList(deep_freeze_json(item) for item in value)
    return value


def validate_json(value: object, *, path: str = "payload") -> object:
    """Reject values that JSON encoders coerce or serialize non-portably."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json(item, path=f"{path}[{index}]")
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise PydanticCustomError(
                    "json_key_type",
                    f"{path} contains a non-string key",
                )
            validate_json(item, path=f"{path}.{key}")
        return value
    raise PydanticCustomError(
        "json_value_type",
        f"{path} contains a non-JSON value: {type(value).__name__}",
    )


__all__ = ["JsonValue", "deep_freeze_json", "validate_json"]
