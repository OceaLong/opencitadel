"""Exact budget arithmetic shared by Token and Decimal money reservations."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from app.domain.evaluation.dataset import ImmutableModel
from app.domain.models.execution_usage import Purpose


def reservation_fits(
    limit: int | Decimal,
    spent: int | Decimal,
    reserved: int | Decimal,
    requested: int | Decimal,
) -> bool:
    if min(limit, spent, reserved, requested) < 0:
        raise ValueError("negative budget value")
    return spent + reserved + requested <= limit


NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class BudgetPrincipal(ImmutableModel):
    user_id: str = Field(min_length=1)
    token_version: NonnegativeInt
    global_role: Literal["user", "admin", "auditor"]
    team_role: Literal["owner", "admin", "member"] | None = None


class BudgetBucket(ImmutableModel):
    key: str = Field(pattern=r"^[0-6]:[^\x1f]+$", max_length=1024)
    slots: PositiveInt | None = None
    tokens: NonnegativeInt | None = None
    money: Money | None = None

    @field_validator("money", mode="before")
    @classmethod
    def exact_money(cls, value):
        if isinstance(value, (bool, float)):
            raise ValueError("budget_money_requires_decimal")  # noqa: TRY004 - Pydantic field validation
        return value


class DirectPhysicalRequest(ImmutableModel):
    request_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    configured_model: str = Field(min_length=1)
    wire_model: str = Field(min_length=1)
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class BudgetDemand(ImmutableModel):
    scope: str = Field(pattern=r"^(user|team):[^\x1f]+$", max_length=261)
    requester: str = Field(min_length=1, max_length=255)
    direct_request: DirectPhysicalRequest | None = None
    principal: BudgetPrincipal | None = None
    purpose: Purpose
    batch_id: str | None = None
    policy_revision: PositiveInt | None = None
    tokens: NonnegativeInt | None = None
    money: Money | None = None
    buckets: tuple[BudgetBucket, ...] = Field(min_length=1, max_length=16)

    @field_validator("money", mode="before")
    @classmethod
    def exact_money(cls, value):
        return BudgetBucket.exact_money(value)

    @field_validator("buckets")
    @classmethod
    def canonical_buckets(cls, value):
        keys = [bucket.key for bucket in value]
        if len(keys) != len(set(keys)):
            raise ValueError("budget_duplicate_bucket")
        if "0:global" not in keys:
            raise ValueError("budget_global_bucket_required")
        return tuple(sorted(value, key=lambda bucket: bucket.key))

    @model_validator(mode="after")
    def principal_matches(self):
        if self.principal is not None and self.principal.user_id != self.requester:
            raise ValueError("budget_requester_mismatch")
        if self.scope.startswith("user:") and self.scope != "user:" + self.requester:
            raise ValueError("budget_scope_mismatch")
        return self


class BudgetSettlement(ImmutableModel):
    tokens: NonnegativeInt | None = None
    money: Money | None = None
    evidence: str | None = None

    @field_validator("money", mode="before")
    @classmethod
    def exact_money(cls, value):
        return BudgetBucket.exact_money(value)


class BudgetPolicy(ImmutableModel):
    revision: PositiveInt = 1
    global_concurrency: PositiveInt | None = None
    user_concurrency: PositiveInt | None = None
    provider_concurrency: PositiveInt | None = None

    @property
    def hard_evaluation_available(self) -> bool:
        return all(
            value is not None
            for value in (self.global_concurrency, self.user_concurrency, self.provider_concurrency)
        )
