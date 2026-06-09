#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Multi-language static analysis for symbols, imports, and call sites."""
import ast
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

from app.domain.models.codebase import (
    CodebaseEdge,
    CodebaseFile,
    CodebaseSymbol,
    EdgeKind,
    SymbolKind,
)

IGNORE_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "target", ".idea", ".vscode", "coverage",
}
IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".o", ".a",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".mp4", ".mp3", ".zip", ".tar", ".gz", ".jar",
    ".lock", ".min.js", ".min.css",
}
MAX_FILE_SIZE = 512_000
MAX_FILES = 5000

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
    files: List[CodebaseFile] = field(default_factory=list)
    symbols: List[CodebaseSymbol] = field(default_factory=list)
    edges: List[CodebaseEdge] = field(default_factory=list)
    language_stats: Dict[str, int] = field(default_factory=dict)
    file_contents: Dict[str, str] = field(default_factory=dict)


def detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return LANG_MAP.get(ext, "text")


def should_skip_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if any(p in IGNORE_DIRS for p in parts):
        return True
    ext = Path(rel_path).suffix.lower()
    return ext in IGNORE_EXTENSIONS


class StaticAnalyzer:
    """Extract symbols and coarse call edges from source files."""

    def analyze_tree(
            self,
            codebase_id: str,
            root_dir: str,
            file_entries: List[Tuple[str, str]],
    ) -> AnalysisResult:
        result = AnalysisResult()

        for rel_path, content in file_entries:
            if should_skip_path(rel_path):
                continue
            if len(content) > MAX_FILE_SIZE:
                content = content[:MAX_FILE_SIZE]
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
            symbols = self._extract_symbols(codebase_id, file_id, rel_path, lang, content)
            result.symbols.extend(symbols)

        result.edges = self.build_call_edges(
            codebase_id, result.symbols, result.files, result.file_contents
        )
        return result

    def _extract_symbols(
            self,
            codebase_id: str,
            file_id: str,
            path: str,
            lang: str,
            content: str,
    ) -> List[CodebaseSymbol]:
        if lang == "python":
            return self._extract_python(codebase_id, file_id, content)
        return self._extract_regex(codebase_id, file_id, lang, content)

    def _extract_python(self, codebase_id: str, file_id: str, content: str) -> List[CodebaseSymbol]:
        symbols: List[CodebaseSymbol] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._extract_regex(codebase_id, file_id, "python", content)

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.class_stack: List[str] = []

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                sym_id = str(uuid.uuid4())
                symbols.append(
                    CodebaseSymbol(
                        id=sym_id,
                        codebase_id=codebase_id,
                        file_id=file_id,
                        name=node.name,
                        kind=SymbolKind.CLASS,
                        signature=f"class {node.name}",
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    )
                )
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._add_func(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._add_func(node)

            def _add_func(self, node) -> None:
                kind = SymbolKind.METHOD if self.class_stack else SymbolKind.FUNCTION
                parent = None
                name = node.name
                if self.class_stack:
                    parent_name = self.class_stack[-1]
                    for s in symbols:
                        if s.name == parent_name and s.kind == SymbolKind.CLASS:
                            parent = s.id
                            break
                args = [a.arg for a in node.args.args]
                sym_id = str(uuid.uuid4())
                symbols.append(
                    CodebaseSymbol(
                        id=sym_id,
                        codebase_id=codebase_id,
                        file_id=file_id,
                        name=name,
                        kind=kind,
                        signature=f"def {name}({', '.join(args)})",
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                        parent_id=parent,
                    )
                )

        Visitor().visit(tree)
        return symbols

    def _extract_regex(
            self,
            codebase_id: str,
            file_id: str,
            lang: str,
            content: str,
    ) -> List[CodebaseSymbol]:
        patterns = [
            (r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", SymbolKind.FUNCTION),
            (r"^\s*(?:export\s+)?class\s+(\w+)", SymbolKind.CLASS),
            (r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?:\{|:)", SymbolKind.METHOD),
            (r"^\s*func\s+(\w+)\s*\(", SymbolKind.FUNCTION),
            (r"^\s*fn\s+(\w+)\s*[\(<]", SymbolKind.FUNCTION),
            (r"^\s*(?:pub\s+)?fn\s+(\w+)\s*\(", SymbolKind.FUNCTION),
            (r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:void|int|String|bool|float|double|\w+)\s+(\w+)\s*\(", SymbolKind.METHOD),
        ]
        symbols: List[CodebaseSymbol] = []
        seen: Set[str] = set()
        for i, line in enumerate(content.splitlines(), start=1):
            for pattern, kind in patterns:
                m = re.match(pattern, line)
                if m:
                    name = m.group(1)
                    if name in seen or name in {"if", "for", "while", "switch", "return"}:
                        continue
                    seen.add(name)
                    sym_id = str(uuid.uuid4())
                    symbols.append(
                        CodebaseSymbol(
                            id=sym_id,
                            codebase_id=codebase_id,
                            file_id=file_id,
                            name=name,
                            kind=kind,
                            signature=line.strip()[:200],
                            start_line=i,
                            end_line=i,
                        )
                    )
                    break
        return symbols

    def build_call_edges(
            self,
            codebase_id: str,
            symbols: List[CodebaseSymbol],
            files: List[CodebaseFile],
            file_contents: Dict[str, str],
    ) -> List[CodebaseEdge]:
        edges: List[CodebaseEdge] = []
        path_by_file_id = {f.id: f.path for f in files}
        name_index: Dict[str, List[str]] = {}
        for s in symbols:
            name_index.setdefault(s.name, []).append(s.id)

        for sym in symbols:
            path = path_by_file_id.get(sym.file_id, "")
            content = file_contents.get(path, "")
            if not content:
                continue
            lines = content.splitlines()
            start = max(0, sym.start_line - 1)
            end = min(len(lines), sym.end_line + 20)
            block = "\n".join(lines[start:end])
            for m in re.finditer(r"\b([a-zA-Z_]\w*)\s*\(", block):
                callee = m.group(1)
                if callee == sym.name or callee in {"if", "for", "while", "switch", "return", "print", "len"}:
                    continue
                dst_ids = name_index.get(callee, [])
                dst_id = dst_ids[0] if dst_ids else None
                edges.append(
                    CodebaseEdge(
                        id=str(uuid.uuid4()),
                        codebase_id=codebase_id,
                        src_symbol_id=sym.id,
                        dst_symbol_id=dst_id,
                        callee_name=callee,
                        kind=EdgeKind.CALL,
                    )
                )
        return edges
