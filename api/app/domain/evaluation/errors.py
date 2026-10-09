"""Stable evaluation failures, without imported bodies in error messages."""

from app.domain.models.resource_pin import ResourceUnavailable


class DatasetConflict(ValueError):
    pass


class DatasetUnavailable(ValueError):
    pass


class DatasetNotFound(DatasetUnavailable):
    """Dataset/version identity is absent or invisible to this scope."""


class ImportInvalid(ValueError):
    pass


class CaseResourceUnavailable(ResourceUnavailable):
    def __init__(self, field: str):
        self.field = field
        super().__init__("resource_unavailable")


class ReplayMismatch(ValueError):
    """Fatal replay failure. Deliberately unrelated to model-correctable tool errors."""

    code = "REPLAY_MISMATCH"

    def __init__(self, reason="recording_mismatch"):
        self.reason = reason
        super().__init__(reason)


class EnvironmentTransportUnknown(RuntimeError):
    """No physical broker response was observed; ownership remains unresolved."""
