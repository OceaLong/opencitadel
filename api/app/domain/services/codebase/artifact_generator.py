#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate cached Mermaid/Markdown artifacts from static analysis facts.

Artifacts in this module are intentionally conservative: diagram-like views are
emitted only when static analysis produced explicit evidence for the underlying
facts.  This avoids presenting generic templates or ordered function lists as if
they were discovered architecture or runtime flow.
"""
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.domain.external.llm import LLM
from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    CodebaseArtifact,
    CodebaseEdge,
    CodebaseFile,
    CodebaseSymbol,
    EdgeKind,
)


@dataclass(frozen=True)
class ArtifactGenerationResult:
    artifacts: List[CodebaseArtifact] = field(default_factory=list)
    unsupported_views: Dict[ArtifactKind, str] = field(default_factory=dict)


class ArtifactGenerator:
    def __init__(self, llm: Optional[LLM] = None) -> None:
        self._llm = llm

    def generate_all(
            self,
            codebase_id: str,
            name: str = "代码库",
            files: Optional[List[CodebaseFile]] = None,
            symbols: Optional[List[CodebaseSymbol]] = None,
            edges: Optional[List[CodebaseEdge]] = None,
            language_stats: Optional[Dict[str, int]] = None,
    ) -> ArtifactGenerationResult:
        files = files or []
        symbols = symbols or []
        edges = edges or []
        language_stats = language_stats or {}

        artifacts: List[CodebaseArtifact] = []
        unsupported_views: Dict[ArtifactKind, str] = {
            ArtifactKind.ARCHITECTURE: "insufficient_evidence",
            ArtifactKind.DATA_FLOW: "unsupported",
            ArtifactKind.CALL_CHAIN: "insufficient_evidence",
            ArtifactKind.FLOWCHART: "unsupported",
        }

        if files or symbols or language_stats:
            artifacts.append(
                self._overview(
                    codebase_id,
                    name,
                    files,
                    symbols,
                    language_stats,
                )
            )
        if files:
            artifacts.append(self._module_dir(codebase_id, files))

        architecture = self._architecture(codebase_id, files, symbols, edges)
        if architecture is not None:
            artifacts.append(architecture)
            unsupported_views.pop(ArtifactKind.ARCHITECTURE, None)

        call_chain = self._call_chain(codebase_id, files, symbols, edges)
        if call_chain is not None:
            artifacts.append(call_chain)
            unsupported_views.pop(ArtifactKind.CALL_CHAIN, None)

        return ArtifactGenerationResult(
            artifacts=artifacts,
            unsupported_views=unsupported_views,
        )

    def generate_all_from_analysis(
            self,
            analysis: object,
            *,
            codebase_id: Optional[str] = None,
            name: str = "代码库",
    ) -> ArtifactGenerationResult:
        files = list(getattr(analysis, "files", []) or [])
        symbols = list(getattr(analysis, "symbols", []) or [])
        edges = list(getattr(analysis, "edges", []) or [])
        language_stats = dict(getattr(analysis, "language_stats", {}) or {})
        resolved_codebase_id = codebase_id or self._infer_codebase_id(
            files,
            symbols,
            edges,
        )
        result = self.generate_all(
            resolved_codebase_id,
            name,
            files,
            symbols,
            edges,
            language_stats,
        )
        version_id = self._infer_version_id(files, symbols, edges)
        if version_id:
            result = ArtifactGenerationResult(
                artifacts=[
                    artifact.model_copy(update={"version_id": version_id})
                    for artifact in result.artifacts
                ],
                unsupported_views=result.unsupported_views,
            )
        return result

    def generate_call_chain(
            self,
            analysis: object,
            *,
            codebase_id: Optional[str] = None,
    ) -> CodebaseArtifact:
        files = list(getattr(analysis, "files", []) or [])
        symbols = list(getattr(analysis, "symbols", []) or [])
        edges = list(getattr(analysis, "edges", []) or [])
        artifact = self._call_chain(
            codebase_id or self._infer_codebase_id(files, symbols, edges),
            files,
            symbols,
            edges,
        )
        if artifact is None:
            raise ValueError("insufficient_evidence")
        version_id = self._infer_version_id(files, symbols, edges)
        if version_id:
            artifact = artifact.model_copy(update={"version_id": version_id})
        return artifact

    def _overview(
            self,
            codebase_id: str,
            name: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            language_stats: Dict[str, int],
    ) -> CodebaseArtifact:
        files_by_id = {file.id: file for file in files}
        lang_lines = ", ".join(
            f"{k}: {v}"
            for k, v in sorted(language_stats.items(), key=lambda x: -x[1])
        )
        top_symbols = sorted(symbols, key=lambda s: s.name)[:30]
        sym_lines = "\n".join(
            f"- `{s.name}` ({s.kind.value})"
            for s in top_symbols
        )
        content = (
            f"# {name} 代码库概览\n\n"
            f"- 文件数: {len(files)}\n"
            f"- 符号数: {len(symbols)}\n"
            f"- 语言分布: {lang_lines}\n\n"
            f"## 主要符号\n{sym_lines}\n"
        )
        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.OVERVIEW,
            format=ArtifactFormat.MARKDOWN,
            title="项目概览",
            content=content,
            meta={
                "files": [
                    self._file_ref(file)
                    for file in files
                ],
                "symbols": [
                    self._symbol_ref(symbol, files_by_id)
                    for symbol in symbols
                ],
            },
            created_at=datetime.now(),
        )

    def _module_dir(self, codebase_id: str, files: List[CodebaseFile]) -> CodebaseArtifact:
        dirs: Dict[str, List[str]] = {}
        for f in files:
            parts = f.path.split("/")
            if len(parts) > 1:
                top = parts[0]
                dirs.setdefault(top, []).append(f.path)
            else:
                dirs.setdefault("(root)", []).append(f.path)

        lines = ["graph TD"]
        for i, (d, paths) in enumerate(sorted(dirs.items())):
            node_id = f"D{i}"
            lines.append(f'    {node_id}["{d} ({len(paths)} files)"]')
        content = "\n".join(lines)
        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.MODULE_DIR,
            format=ArtifactFormat.MERMAID,
            title="功能目录",
            content=content,
            meta={
                "dirs": {k: len(v) for k, v in dirs.items()},
                "paths": [
                    self._file_ref(file)
                    for file in files
                ],
            },
            created_at=datetime.now(),
        )

    def _architecture(
            self,
            codebase_id: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            edges: List[CodebaseEdge],
    ) -> Optional[CodebaseArtifact]:
        symbols_by_id = {symbol.id: symbol for symbol in symbols}
        files_by_id = {file.id: file for file in files}
        evidence_edges = [
            edge for edge in edges
            if self._edge_kind_value(edge.kind) in {"import", "dependency"}
            and edge.evidence
        ]
        if not evidence_edges:
            return None

        module_nodes: Dict[str, str] = {}
        meta_edges: List[Dict[str, Any]] = []
        lines = ["graph TB"]
        seen_edges: set[tuple[str, str]] = set()
        for edge in evidence_edges[:120]:
            src_symbol = symbols_by_id.get(edge.src_symbol_id)
            if src_symbol is None:
                continue
            src_file = files_by_id.get(src_symbol.file_id)
            if src_file is None:
                continue
            src_module = self._module_name(src_file.path)

            dst_module = ""
            dst_symbol = (
                symbols_by_id.get(edge.dst_symbol_id)
                if edge.dst_symbol_id
                else None
            )
            if dst_symbol is not None:
                dst_file = files_by_id.get(dst_symbol.file_id)
                if dst_file is not None:
                    dst_module = self._module_name(dst_file.path)
            if not dst_module and edge.callee_name:
                dst_module = edge.callee_name
            if not dst_module or src_module == dst_module:
                continue

            src_node = self._module_node_id(src_module, module_nodes)
            dst_node = self._module_node_id(dst_module, module_nodes)
            edge_key = (src_node, dst_node)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            lines.append(f"    {src_node} --> {dst_node}")
            meta_edges.append(
                self._edge_ref(
                    edge,
                    symbols_by_id,
                    files_by_id,
                    src_module=src_module,
                    dst_module=dst_module,
                )
            )

        if not meta_edges:
            return None

        node_lines = [
            f'    {node_id}["{self._escape_label(module)}"]'
            for module, node_id in sorted(module_nodes.items())
        ]
        content = "\n".join([lines[0], *node_lines, *lines[1:]])
        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.ARCHITECTURE,
            format=ArtifactFormat.MERMAID,
            title="架构图",
            content=content,
            meta={
                "nodes": [
                    {"module": module, "node_id": node_id}
                    for module, node_id in sorted(module_nodes.items())
                ],
                "edges": meta_edges,
            },
            created_at=datetime.now(),
        )

    def _call_chain(
            self,
            codebase_id: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            edges: List[CodebaseEdge],
    ) -> Optional[CodebaseArtifact]:
        sym_by_id = {s.id: s for s in symbols}
        file_by_id = {f.id: f for f in files}
        evidence_edges = [
            edge for edge in edges
            if self._edge_kind_value(edge.kind) == EdgeKind.CALL.value
            and edge.evidence
            and edge.src_symbol_id in sym_by_id
        ]
        if not evidence_edges:
            return None

        lines = ["graph LR"]
        seen_nodes: set[str] = set()
        seen_edges: set[str] = set()
        meta_edges: List[Dict[str, Any]] = []
        for edge in evidence_edges[:80]:
            src = sym_by_id.get(edge.src_symbol_id)
            if not src:
                continue
            src_node = self._symbol_node_id(src)
            if src_node not in seen_nodes:
                lines.append(f'    {src_node}["{self._escape_label(src.name)}"]')
                seen_nodes.add(src_node)
            if edge.dst_symbol_id:
                dst = sym_by_id.get(edge.dst_symbol_id)
                if dst:
                    dst_node = self._symbol_node_id(dst)
                    if dst_node not in seen_nodes:
                        lines.append(f'    {dst_node}["{self._escape_label(dst.name)}"]')
                        seen_nodes.add(dst_node)
                    edge_key = f"{src_node}->{dst_node}"
                    if edge_key not in seen_edges:
                        lines.append(f"    {src_node} --> {dst_node}")
                        seen_edges.add(edge_key)
                        meta_edges.append(
                            self._edge_ref(edge, sym_by_id, file_by_id)
                        )
            else:
                callee_node = self._callee_node_id(edge.callee_name)
                if callee_node not in seen_nodes:
                    lines.append(
                        f'    {callee_node}["{self._escape_label(edge.callee_name)}"]'
                    )
                    seen_nodes.add(callee_node)
                edge_key = f"{src_node}->{callee_node}"
                if edge_key not in seen_edges:
                    lines.append(f"    {src_node} --> {callee_node}")
                    seen_edges.add(edge_key)
                    meta_edges.append(
                        self._edge_ref(edge, sym_by_id, file_by_id)
                    )

        if not meta_edges:
            return None
        node_locations: List[Dict[str, object]] = []
        node_refs: List[Dict[str, object]] = []
        seen_symbol_ids: set[str] = set()
        for edge in evidence_edges[:40]:
            for sym_id in (edge.src_symbol_id, edge.dst_symbol_id):
                if not sym_id or sym_id in seen_symbol_ids:
                    continue
                sym = sym_by_id.get(sym_id)
                if not sym:
                    continue
                file = file_by_id.get(sym.file_id)
                path = file.path if file else ""
                if not path:
                    continue
                seen_symbol_ids.add(sym_id)
                node_locations.append(
                    {
                        "symbol": sym.name,
                        "symbol_id": sym.id,
                        "path": path,
                        "line": sym.start_line,
                    }
                )
                node_refs.append(self._symbol_ref(sym, file_by_id))

        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.CALL_CHAIN,
            format=ArtifactFormat.MERMAID,
            title="调用链图",
            content="\n".join(lines),
            meta={
                "node_locations": node_locations,
                "nodes": node_refs,
                "edges": meta_edges,
            },
            created_at=datetime.now(),
        )

    @staticmethod
    def _infer_codebase_id(
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            edges: List[CodebaseEdge],
    ) -> str:
        for item in [*files, *symbols, *edges]:
            codebase_id = getattr(item, "codebase_id", "")
            if codebase_id:
                return codebase_id
        return "cb1"

    @staticmethod
    def _infer_version_id(
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            edges: List[CodebaseEdge],
    ) -> str:
        for item in [*files, *symbols, *edges]:
            version_id = getattr(item, "version_id", "")
            if version_id:
                return version_id
        for edge in edges:
            for evidence in edge.evidence:
                if evidence.version_id:
                    return evidence.version_id
        return ""

    @staticmethod
    def _edge_kind_value(kind: object) -> str:
        return getattr(kind, "value", str(kind))

    @staticmethod
    def _file_ref(file: CodebaseFile) -> Dict[str, Any]:
        return {
            "file_id": file.id,
            "version_id": file.version_id,
            "path": file.path,
            "language": file.language,
            "size": file.size,
            "sha": file.sha,
        }

    def _symbol_ref(
            self,
            symbol: CodebaseSymbol,
            files_by_id: Dict[str, CodebaseFile],
    ) -> Dict[str, Any]:
        file = files_by_id.get(symbol.file_id)
        return {
            "symbol": symbol.name,
            "symbol_id": symbol.id,
            "qualified_name": symbol.qualified_name,
            "kind": symbol.kind.value,
            "version_id": symbol.version_id,
            "file_id": symbol.file_id,
            "path": file.path if file else "",
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
            "analyzer": symbol.parser,
            "confidence": symbol.confidence,
        }

    def _edge_ref(
            self,
            edge: CodebaseEdge,
            symbols_by_id: Dict[str, CodebaseSymbol],
            files_by_id: Dict[str, CodebaseFile],
            *,
            src_module: str = "",
            dst_module: str = "",
    ) -> Dict[str, Any]:
        src_symbol = symbols_by_id.get(edge.src_symbol_id)
        dst_symbol = (
            symbols_by_id.get(edge.dst_symbol_id)
            if edge.dst_symbol_id
            else None
        )
        src_file = files_by_id.get(src_symbol.file_id) if src_symbol else None
        dst_file = files_by_id.get(dst_symbol.file_id) if dst_symbol else None
        evidence_refs = [
            evidence.model_dump(mode="json")
            for evidence in edge.evidence
        ]
        return {
            "edge_id": edge.id,
            "kind": self._edge_kind_value(edge.kind),
            "version_id": edge.version_id
            or (evidence_refs[0].get("version_id") if evidence_refs else None),
            "src_symbol_id": edge.src_symbol_id,
            "dst_symbol_id": edge.dst_symbol_id,
            "callee_name": edge.callee_name,
            "resolution": edge.resolution,
            "confidence": edge.confidence,
            "src": self._edge_symbol_ref(src_symbol, src_file),
            "dst": self._edge_symbol_ref(dst_symbol, dst_file),
            "src_module": src_module,
            "dst_module": dst_module,
            "evidence_refs": evidence_refs,
        }

    @staticmethod
    def _edge_symbol_ref(
            symbol: Optional[CodebaseSymbol],
            file: Optional[CodebaseFile],
    ) -> Optional[Dict[str, Any]]:
        if symbol is None:
            return None
        return {
            "symbol": symbol.name,
            "symbol_id": symbol.id,
            "qualified_name": symbol.qualified_name,
            "version_id": symbol.version_id,
            "file_id": symbol.file_id,
            "path": file.path if file else "",
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
            "analyzer": symbol.parser,
            "confidence": symbol.confidence,
        }

    @staticmethod
    def _module_name(path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if len(parts) > 1:
            return parts[0]
        return "(root)"

    def _module_node_id(
            self,
            module: str,
            module_nodes: Dict[str, str],
    ) -> str:
        node_id = module_nodes.get(module)
        if node_id:
            return node_id
        node_id = f"M{len(module_nodes)}_{self._safe_id(module)}"
        module_nodes[module] = node_id
        return node_id

    def _symbol_node_id(self, symbol: CodebaseSymbol) -> str:
        return f"S_{self._safe_id(symbol.id)}"

    def _callee_node_id(self, callee: str) -> str:
        digest = hashlib.sha1(callee.encode("utf-8")).hexdigest()[:10]
        return f"C_{self._safe_id(callee)[:30]}_{digest}"

    @staticmethod
    def _safe_id(value: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z_]", "_", value or "unknown")
        if not safe or safe[0].isdigit():
            safe = f"_{safe}"
        return safe[:80]

    @staticmethod
    def _escape_label(value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace('"', '\\"')
