"""Private operand sink supplied only by trusted observer composition."""

from typing import Protocol


class OriginalEvidence(Protocol):
    def retain(self, family: str, value: object) -> None: ...

    def reserve_state(self, items: int, *, bytes_per_item: int = 256) -> None: ...


_EVIDENCE_KEY = "_capacity_original_evidence"


def session_evidence(session) -> OriginalEvidence | None:
    """Trusted dedicated-session owner; ordinary business sessions have no sink."""
    synchronous = getattr(session, "sync_session", session)
    owner = getattr(synchronous, "_original_evidence_owner", None)
    selected = getattr(synchronous, "info", {}).get(_EVIDENCE_KEY)
    if owner is not None:
        if selected is not owner or getattr(synchronous, "evidence", None) is not owner:
            raise ValueError("private original evidence owner missing or replaced")
        return owner
    if selected is not None:
        raise ValueError("private evidence sink lacks session owner")
    return None


def retain_read(
    session,
    family: str,
    operation: str,
    identity: object,
    value: object,
    *,
    source_result=None,
) -> None:
    owner = session_evidence(session)
    if owner is not None:
        synchronous = getattr(session, "sync_session", session)
        if source_result is None:
            raise ValueError("original repository source Result required")
        result_observation = getattr(synchronous, "sql_for_result", None)
        if not callable(result_observation):
            raise ValueError("original SQL result owner missing")
        token, observation = result_observation(source_result)
        ordinal = owner.sql_ordinal(token, observation)
        owner.retain(
            family,
            {
                "operation": operation,
                "identity": identity,
                "value": value,
                "read": {
                    "sql_index": ordinal,
                    "uow": observation["uow"],
                    "snapshot": observation["snapshot"],
                },
            },
        )
        owner.reserve_state(1)
