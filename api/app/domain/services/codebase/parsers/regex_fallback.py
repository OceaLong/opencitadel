#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Conservative regex parser fallback for languages without a parser."""
from __future__ import annotations

import re
from typing import Iterable

from app.domain.models.codebase import SymbolKind
from app.domain.services.codebase.parsers.base import (
    ParsedCallSite,
    ParsedFile,
    ParsedSymbol,
    SourceRange,
)


_CONTROL_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "catch",
    "print",
    "len",
}


class RegexFallbackParser:
    def __init__(
            self,
            *,
            parser_name: str = "regex",
            confidence: float = 0.3,
    ) -> None:
        self.parser_name = parser_name
        self.confidence = confidence

    def parse(self, path: str, content: str, language: str = "text") -> ParsedFile:
        lines = content.splitlines()
        symbols: list[ParsedSymbol] = []
        calls: list[ParsedCallSite] = []
        seen: set[str] = set()

        class_ranges = self._extract_classes(lines, symbols, seen)
        self._extract_methods(lines, class_ranges, symbols, seen)
        self._extract_functions(lines, class_ranges, symbols, seen)
        self._extract_calls(lines, symbols, calls)
        return ParsedFile(symbols=symbols, calls=calls)

    def _extract_classes(
            self,
            lines: list[str],
            symbols: list[ParsedSymbol],
            seen: set[str],
    ) -> list[tuple[str, int, int]]:
        class_ranges: list[tuple[str, int, int]] = []
        for idx, line in enumerate(lines):
            for match in re.finditer(r"\bclass\s+([A-Za-z_]\w*)", line):
                name = match.group(1)
                start = idx + 1
                end = self._brace_end_line(lines, idx, match.start())
                qualified = name
                if qualified in seen:
                    continue
                seen.add(qualified)
                class_ranges.append((name, start, end))
                symbols.append(
                    ParsedSymbol(
                        name=name,
                        qualified_name=qualified,
                        kind=SymbolKind.CLASS,
                        signature=line.strip()[:200],
                        range=SourceRange(start, end),
                        parser=self.parser_name,
                        confidence=self.confidence,
                    )
                )
        return class_ranges

    def _extract_methods(
            self,
            lines: list[str],
            class_ranges: Iterable[tuple[str, int, int]],
            symbols: list[ParsedSymbol],
            seen: set[str],
    ) -> None:
        for class_name, start, end in class_ranges:
            for idx in range(start - 1, min(end, len(lines))):
                line = lines[idx]
                for match in re.finditer(
                    r"\b(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:\{|:)",
                    line,
                ):
                    name = match.group(1)
                    if name in _CONTROL_NAMES or name == class_name:
                        continue
                    qualified = f"{class_name}.{name}"
                    if qualified in seen:
                        continue
                    seen.add(qualified)
                    method_end = self._brace_end_line(lines, idx, match.start())
                    symbols.append(
                        ParsedSymbol(
                            name=name,
                            qualified_name=qualified,
                            kind=SymbolKind.METHOD,
                            signature=line.strip()[:200],
                            range=SourceRange(idx + 1, method_end),
                            parser=self.parser_name,
                            confidence=self.confidence,
                            parent_qualified_name=class_name,
                        )
                    )

    def _extract_functions(
            self,
            lines: list[str],
            class_ranges: Iterable[tuple[str, int, int]],
            symbols: list[ParsedSymbol],
            seen: set[str],
    ) -> None:
        ranges = list(class_ranges)
        patterns = [
            r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(",
            r"\bfunc\s+([A-Za-z_]\w*)\s*\(",
            r"\b(?:pub\s+)?fn\s+([A-Za-z_]\w*)\s*(?:\(|<)",
            r"^\s*(?:public|private|protected|static|\s)*"
            r"(?:void|int|String|bool|float|double|[A-Za-z_]\w*)\s+"
            r"([A-Za-z_]\w*)\s*\(",
        ]
        for idx, line in enumerate(lines):
            line_no = idx + 1
            in_class = any(start <= line_no <= end for _name, start, end in ranges)
            for pattern in patterns:
                for match in re.finditer(pattern, line):
                    name = match.group(1)
                    if name in _CONTROL_NAMES:
                        continue
                    if in_class and not line.lstrip().startswith("function "):
                        continue
                    qualified = name
                    if qualified in seen:
                        continue
                    seen.add(qualified)
                    end_line = self._brace_end_line(lines, idx, match.start())
                    symbols.append(
                        ParsedSymbol(
                            name=name,
                            qualified_name=qualified,
                            kind=SymbolKind.FUNCTION,
                            signature=line.strip()[:200],
                            range=SourceRange(line_no, end_line),
                            parser=self.parser_name,
                            confidence=self.confidence,
                        )
                    )
                    break

    def _extract_calls(
            self,
            lines: list[str],
            symbols: list[ParsedSymbol],
            calls: list[ParsedCallSite],
    ) -> None:
        for symbol in symbols:
            if symbol.kind not in {SymbolKind.FUNCTION, SymbolKind.METHOD}:
                continue
            block_lines = lines[
                max(0, symbol.range.start_line - 1): min(len(lines), symbol.range.end_line)
            ]
            for offset, line in enumerate(block_lines):
                line_no = symbol.range.start_line + offset
                for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", line):
                    callee = match.group(1)
                    if callee == symbol.name or callee in _CONTROL_NAMES:
                        continue
                    calls.append(
                        ParsedCallSite(
                            caller_qualified_name=symbol.qualified_name,
                            callee_name=callee,
                            line=line_no,
                            parser=self.parser_name,
                            confidence=max(0.1, self.confidence - 0.05),
                        )
                    )

    @staticmethod
    def _brace_end_line(
            lines: list[str],
            start_idx: int,
            start_col: int = 0,
    ) -> int:
        balance = 0
        saw_open = False
        for idx in range(start_idx, len(lines)):
            segment = lines[idx][start_col:] if idx == start_idx else lines[idx]
            for char in segment:
                if char == "{":
                    balance += 1
                    saw_open = True
                elif char == "}":
                    balance -= 1
                    if saw_open and balance <= 0:
                        return idx + 1
            if saw_open and balance <= 0:
                return idx + 1
        return start_idx + 1
