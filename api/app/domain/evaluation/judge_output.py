"""Strict portable JSON contract; provider schema hints never grant authority."""

import json
from typing import Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from app.domain.evaluation.dataset import ImmutableModel


def validate_score(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 4:
        raise ValueError("score must be an integer from 0 to 4")
    return value


class JudgeDimension(ImmutableModel):
    name: StrictStr = Field(min_length=1, max_length=100)
    score: StrictInt | None
    reason: StrictStr = Field(min_length=1, max_length=2000)
    evidence: tuple[StrictStr, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if not self.name.strip() or not self.reason.strip():
            raise ValueError("judge_reason_or_name_empty")
        if self.score is not None:
            validate_score(self.score)
        if len(set(self.evidence)) != len(self.evidence) or any(
            not e or len(e) > 255 for e in self.evidence
        ):
            raise ValueError("invalid_evidence_ids")
        return self


class JudgeOutput(ImmutableModel):
    status: Literal["complete", "not_evaluable"]
    dimensions: tuple[JudgeDimension, ...] = Field(min_length=1, max_length=20)
    unavailable_reason: StrictStr | None = Field(max_length=2000)

    @model_validator(mode="after")
    def valid(self):
        if len({d.name for d in self.dimensions}) != len(self.dimensions):
            raise ValueError("duplicate_dimension")
        unavailable = any(d.score is None for d in self.dimensions)
        if self.status == "complete":
            if unavailable or self.unavailable_reason is not None:
                raise ValueError("invalid_complete_output")
        elif not unavailable or not self.unavailable_reason or not self.unavailable_reason.strip():
            raise ValueError("invalid_unavailable_output")
        return self


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite_json_number")


def parse_judge_output(text: str) -> JudgeOutput:
    if not isinstance(text, str) or len(text.encode()) > 128 * 1024:
        raise ValueError("judge_output_size")
    try:
        raw = json.loads(text, object_pairs_hook=_object, parse_constant=_nonfinite)
    except RecursionError as error:
        raise ValueError("judge_output_depth") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("dimensions"), list):
        raise ValueError("judge_output_shape")  # noqa: TRY004
    if any(
        not isinstance(d, dict) or not isinstance(d.get("evidence"), list)
        for d in raw["dimensions"]
    ):
        raise ValueError("judge_dimension_shape")
    return JudgeOutput.model_validate(raw)
