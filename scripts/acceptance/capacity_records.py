"""Shared strict capacity record and scalar definitions."""

from typing import Annotated, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

Nat = Annotated[StrictInt, Field(ge=0)]
Pos = Annotated[StrictInt, Field(gt=0)]
Number = Annotated[StrictFloat | StrictInt, Field(ge=0, allow_inf_nan=False)]
ID = Annotated[StrictStr, Field(min_length=1, max_length=255)]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)

    @model_validator(mode="before")
    @classmethod
    def exact_numeric_literals(cls, value):
        if isinstance(value, dict):
            for name, field in cls.model_fields.items():
                if name in value and get_origin(field.annotation) is Literal:
                    choices = get_args(field.annotation)
                    if (
                        choices
                        and all(type(x) is bool for x in choices)
                        and type(value[name]) is not bool
                    ):
                        raise ValueError(f"{name}: boolean literal requires bool")
                    if (
                        choices
                        and all(type(x) is int for x in choices)
                        and type(value[name]) is not int
                    ):
                        raise ValueError(f"{name}: numeric literal requires integer, never bool")
        return value
