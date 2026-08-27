"""Stable fail-closed Runtime Policy errors."""

from typing import TYPE_CHECKING

from app.domain.errors import AppException

if TYPE_CHECKING:
    from app.domain.runtime_policy.revision import RuntimePolicyHead


class RuntimePolicyIntegrityError(AppException):
    def __init__(self, msg: str = "Runtime Policy integrity validation failed") -> None:
        super().__init__(
            code=503,
            status_code=503,
            msg=msg,
            error_key="runtimePolicy.integrity",
        )


class RuntimePolicyUnavailableError(AppException):
    def __init__(
        self,
        msg: str = "Runtime Policy is unavailable",
        *,
        transient: bool = False,
    ) -> None:
        self.transient = transient
        super().__init__(
            code=503,
            status_code=503,
            msg=msg,
            data={"transient": transient},
            error_key="runtimePolicy.unavailable",
        )


class RuntimePolicyStaleError(AppException):
    def __init__(self, *, age_seconds: float) -> None:
        super().__init__(
            code=503,
            status_code=503,
            msg="Runtime Policy cache is stale",
            data={"age_seconds": age_seconds},
            error_key="runtimePolicy.stale",
        )


class RuntimePolicyHeadConflictError(AppException):
    def __init__(self, current: "RuntimePolicyHead") -> None:
        super().__init__(
            code=409,
            status_code=409,
            msg="Runtime Policy head changed; reload and retry",
            data=current.model_dump(mode="json"),
            error_key="runtimePolicy.headConflict",
        )


__all__ = [
    "RuntimePolicyHeadConflictError",
    "RuntimePolicyIntegrityError",
    "RuntimePolicyStaleError",
    "RuntimePolicyUnavailableError",
]
