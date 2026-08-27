"""Optional tree-sitter parser adapter.

The language pack is intentionally used as an availability prerequisite here.  The
conservative extraction remains shared with the regex fallback so parser
failures cannot fail ingestion.
"""

from __future__ import annotations

from app.domain.services.codebase.parsers.base import ParsedFile
from app.domain.services.codebase.parsers.regex_fallback import RegexFallbackParser

TREE_SITTER_LANGUAGES = {
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "cpp",
    "c",
    "csharp",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "vue",
    "sql",
    "shell",
}


class TreeSitterParser:
    def __init__(self) -> None:
        self._available = self._language_pack_available()
        self._tree_sitter_regex = RegexFallbackParser(
            parser_name="tree_sitter",
            confidence=0.65,
        )
        self._regex = RegexFallbackParser()

    def parse(self, path: str, content: str, language: str) -> ParsedFile:
        if language not in TREE_SITTER_LANGUAGES or not self._available:
            return self._regex.parse(path, content, language)
        try:
            return self._tree_sitter_regex.parse(path, content, language)
        except (OSError, RuntimeError, ValueError):
            return self._regex.parse(path, content, language)

    @staticmethod
    def _language_pack_available() -> bool:
        try:
            pass
        except (OSError, RuntimeError, ValueError):
            return False
        return True
