"""Multi-language static analysis for symbols, imports, and call sites."""

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.models.codebase import (
    CodebaseEdge,
    CodebaseFile,
    CodebaseSymbol,
    CodeEvidenceRef,
    EdgeKind,
)
from app.domain.runtime_policy import CodebaseAnalysisPolicy
from app.domain.services.codebase.parsers.base import ParsedCallSite, ParsedFile
from app.domain.services.codebase.parsers.python_parser import PythonParser
from app.domain.services.codebase.parsers.regex_fallback import RegexFallbackParser
from app.domain.services.codebase.parsers.tree_sitter_parser import TreeSitterParser

IGNORE_DIRS = {
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    "target",
    ".idea",
    ".vscode",
    "coverage",
}
IGNORE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".o",
    ".a",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".zip",
    ".tar",
    ".gz",
    ".jar",
    ".lock",
    ".min.js",
    ".min.css",
}
LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".vue": "vue",
    ".sql": "sql",
    ".sh": "shell",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}


@dataclass
class AnalysisResult:
    files: list[CodebaseFile] = field(default_factory=list)
    symbols: list[CodebaseSymbol] = field(default_factory=list)
    edges: list[CodebaseEdge] = field(default_factory=list)
    language_stats: dict[str, int] = field(default_factory=dict)
    file_contents: dict[str, str] = field(default_factory=dict)


def detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return LANG_MAP.get(ext, "text")


def should_skip_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if any(p in IGNORE_DIRS for p in parts):
        return True
    lowered = rel_path.lower()
    return any(lowered.endswith(ext) for ext in IGNORE_EXTENSIONS)


class StaticAnalyzer:
    """Extract symbols and coarse call edges from source files."""

    def __init__(self, *, policy: CodebaseAnalysisPolicy) -> None:
        self._policy = policy
        self._python = PythonParser()
        self._tree_sitter = TreeSitterParser()
        self._regex = RegexFallbackParser()

    def analyze(
        self,
        files: dict[str, str],
        *,
        codebase_id: str = "cb1",
        version_id: str | None = None,
    ) -> AnalysisResult:
        return self.analyze_tree(
            codebase_id,
            "",
            list(files.items()),
            version_id=version_id,
        )

    def analyze_tree(
        self,
        codebase_id: str,
        root_dir: str,
        file_entries: list[tuple[str, str]],
        version_id: str | None = None,
    ) -> AnalysisResult:
        result = AnalysisResult()
        parsed_calls: list[tuple[ParsedCallSite, str, str]] = []

        for rel_path, content in file_entries:
            if should_skip_path(rel_path):
                continue
            if len(content) > self._policy.max_file_size_bytes:
                content = content[: self._policy.max_file_size_bytes]
            lang = detect_language(rel_path)
            result.language_stats[lang] = result.language_stats.get(lang, 0) + 1
            sha = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16]
            file_id = str(uuid.uuid4())
            result.files.append(
                CodebaseFile(
                    id=file_id,
                    codebase_id=codebase_id,
                    path=rel_path,
                    language=lang,
                    size=len(content.encode("utf-8", errors="ignore")),
                    sha=sha,
                )
            )
            result.file_contents[rel_path] = content
            parsed = self._parse_file(rel_path, lang, content)
            symbols = self._to_domain_symbols(
                codebase_id,
                file_id,
                parsed,
                version_id=version_id,
            )
            result.symbols.extend(symbols)
            parsed_calls.extend((call, file_id, rel_path) for call in parsed.calls)

        result.edges = self.build_call_edges(
            codebase_id,
            result.symbols,
            result.files,
            result.file_contents,
            parsed_calls,
        )
        return result

    def _parse_file(self, path: str, lang: str, content: str) -> ParsedFile:
        if lang == "python":
            try:
                return self._python.parse(path, content)
            except SyntaxError:
                return self._regex.parse(path, content, lang)
        return self._tree_sitter.parse(path, content, lang)

    @staticmethod
    def _to_domain_symbols(
        codebase_id: str,
        file_id: str,
        parsed: ParsedFile,
        *,
        version_id: str | None,
    ) -> list[CodebaseSymbol]:
        id_by_qualified = {symbol.qualified_name: str(uuid.uuid4()) for symbol in parsed.symbols}
        return [
            CodebaseSymbol(
                id=id_by_qualified[symbol.qualified_name],
                codebase_id=codebase_id,
                version_id=version_id,
                file_id=file_id,
                name=symbol.name,
                qualified_name=symbol.qualified_name or symbol.name,
                kind=symbol.kind,
                signature=symbol.signature,
                start_line=symbol.range.start_line,
                end_line=symbol.range.end_line,
                parent_id=id_by_qualified.get(symbol.parent_qualified_name or ""),
                parser=symbol.parser,
                confidence=symbol.confidence,
            )
            for symbol in parsed.symbols
        ]

    def build_call_edges(
        self,
        codebase_id: str,
        symbols: list[CodebaseSymbol],
        files: list[CodebaseFile],
        file_contents: dict[str, str],
        parsed_calls: list[tuple[ParsedCallSite, str, str]],
    ) -> list[CodebaseEdge]:
        edges: list[CodebaseEdge] = []
        path_by_file_id = {f.id: f.path for f in files}
        symbol_by_qualified = {s.qualified_name: s for s in symbols}
        name_index: dict[str, list[CodebaseSymbol]] = {}
        for s in symbols:
            name_index.setdefault(s.name, []).append(s)

        seen: set[tuple[str, str, int]] = set()
        for call, _file_id, path in parsed_calls:
            src = symbol_by_qualified.get(call.caller_qualified_name)
            if src is None:
                continue
            key = (src.id, call.callee_name, call.line)
            if key in seen:
                continue
            seen.add(key)
            dst, resolution = self._resolve_call(
                src,
                name_index.get(call.callee_name, []),
            )
            confidence = call.confidence if resolution == "resolved" else min(call.confidence, 0.45)
            edges.append(
                CodebaseEdge(
                    id=str(uuid.uuid4()),
                    codebase_id=codebase_id,
                    version_id=src.version_id,
                    src_symbol_id=src.id,
                    dst_symbol_id=dst.id if dst else None,
                    callee_name=call.callee_name,
                    kind=call.kind or EdgeKind.CALL,
                    resolution=resolution,
                    confidence=confidence,
                    evidence=[
                        CodeEvidenceRef(
                            version_id=src.version_id or "",
                            file_id=src.file_id,
                            path=path or path_by_file_id.get(src.file_id, ""),
                            start_line=call.line,
                            end_line=call.line,
                            symbol_id=src.id,
                            analyzer=call.parser or "static_analyzer",
                            confidence=confidence,
                        )
                    ],
                )
            )
        return edges

    @staticmethod
    def _resolve_call(
        src: CodebaseSymbol,
        candidates: list[CodebaseSymbol],
    ) -> tuple[CodebaseSymbol | None, str]:
        candidates = [candidate for candidate in candidates if candidate.id != src.id]
        if not candidates:
            return None, "unresolved"
        same_file = [candidate for candidate in candidates if candidate.file_id == src.file_id]
        if len(same_file) == 1:
            return same_file[0], "resolved"
        if len(candidates) == 1:
            return candidates[0], "resolved"
        return None, "ambiguous"
