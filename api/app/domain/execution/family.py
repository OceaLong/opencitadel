"""Closed set of universal Run families."""

from enum import StrEnum


class RunFamily(StrEnum):
    AGENT = "agent"
    ASK = "ask"
    KB_INGEST = "kb_ingest"
    AUTOMATION = "automation"
    PATROL = "patrol"
    REMEDIATION = "remediation"


__all__ = ["RunFamily"]
