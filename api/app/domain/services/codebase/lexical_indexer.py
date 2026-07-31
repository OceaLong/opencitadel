#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Identifier-aware lexical documents for codebase search."""
from __future__ import annotations

import re
from collections.abc import Iterable


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_CAMEL_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_CONTENT_LIMIT = 8000


class CodebaseLexicalIndexer:
    """Build simple-tsvector-friendly code search documents."""

    def search_text(
        self,
        *,
        path: str,
        symbols: Iterable[str] = (),
        content: str = "",
    ) -> str:
        tokens: list[str] = []
        self._add_identifier(tokens, path)
        for segment in re.split(r"[/\\.\-]+", path or ""):
            self._add_identifier(tokens, segment)
        for symbol in symbols:
            self._add_identifier(tokens, symbol)
        bounded_content = (content or "")[:_CONTENT_LIMIT]
        for raw in _TOKEN_RE.findall(bounded_content):
            self._add_identifier(tokens, raw)
        return " ".join(dict.fromkeys(token for token in tokens if token))

    def _add_identifier(self, tokens: list[str], value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        normalized = text.lower()
        if self._safe_token(normalized):
            tokens.append(normalized)
        compact = re.sub(r"[^A-Za-z0-9]+", "", text).lower()
        if compact:
            tokens.append(compact)
        for raw in _TOKEN_RE.findall(text):
            lowered = raw.lower()
            if self._safe_token(lowered):
                tokens.append(lowered)
            for part in re.split(r"[_\W]+", raw):
                self._add_camel_parts(tokens, part)

    @staticmethod
    def _add_camel_parts(tokens: list[str], value: str) -> None:
        if not value:
            return
        for part in _CAMEL_BOUNDARY_RE.split(value):
            lowered = part.lower()
            if lowered:
                tokens.append(lowered)

    @staticmethod
    def _safe_token(token: str) -> bool:
        return bool(token) and not any(char.isspace() for char in token)
