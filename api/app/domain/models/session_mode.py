"""Conversation execution modes independent of resource types."""

from enum import StrEnum


class SessionMode(StrEnum):
    ASK = "ask"
    AGENT = "agent"


__all__ = ["SessionMode"]
