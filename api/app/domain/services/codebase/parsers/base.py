"""Parser adapter value objects."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models.codebase import EdgeKind, SymbolKind


@dataclass(frozen=True)
class SourceRange:
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: SymbolKind
    signature: str
    range: SourceRange
    parser: str
    confidence: float
    parent_qualified_name: str | None = None


@dataclass(frozen=True)
class ParsedCallSite:
    caller_qualified_name: str
    callee_name: str
    line: int
    kind: EdgeKind = EdgeKind.CALL
    parser: str = ""
    confidence: float = 0.6


@dataclass(frozen=True)
class ParsedFile:
    symbols: list[ParsedSymbol] = field(default_factory=list)
    calls: list[ParsedCallSite] = field(default_factory=list)
