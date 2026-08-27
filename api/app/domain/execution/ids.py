"""Strongly named execution identifiers."""

from typing import NewType
from uuid import UUID

CommandId = NewType("CommandId", UUID)
EventId = NewType("EventId", UUID)
StreamId = NewType("StreamId", str)

__all__ = ["CommandId", "EventId", "StreamId"]
