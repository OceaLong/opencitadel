#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate cached Mermaid/Markdown artifacts from static analysis facts."""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from app.domain.external.llm import LLM
from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    CodebaseArtifact,
    CodebaseEdge,
    CodebaseFile,
    CodebaseSymbol,
)
class ArtifactGenerator:
    def __init__(self, llm: Optional[LLM] = None) -> None:
        self._llm = llm

    def generate_all(
            self,
            codebase_id: str,
            name: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            edges: List[CodebaseEdge],
            language_stats: Dict[str, int],
    ) -> List[CodebaseArtifact]:
        artifacts: List[CodebaseArtifact] = []
        artifacts.append(self._overview(codebase_id, name, files, symbols, language_stats))
        artifacts.append(self._module_dir(codebase_id, files))
        artifacts.append(self._architecture(codebase_id, files, symbols, language_stats))
        artifacts.append(self._data_flow(codebase_id, files, symbols))
        artifacts.append(self._call_chain(codebase_id, files, symbols, edges))
        artifacts.append(self._flowchart(codebase_id, symbols))
        return artifacts

    def _overview(
            self,
            codebase_id: str,
            name: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            language_stats: Dict[str, int],
    ) -> CodebaseArtifact:
        lang_lines = ", ".join(f"{k}: {v}" for k, v in sorted(language_stats.items(), key=lambda x: -x[1]))
        top_symbols = sorted(symbols, key=lambda s: s.name)[:30]
        sym_lines = "\n".join(f"- `{s.name}` ({s.kind.value})" for s in top_symbols)
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
            meta={"dirs": {k: len(v) for k, v in dirs.items()}},
            created_at=datetime.now(),
        )

    def _architecture(
            self,
            codebase_id: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            language_stats: Dict[str, int],
    ) -> CodebaseArtifact:
        layers = {
            "ui": [],
            "api": [],
            "domain": [],
            "infra": [],
            "other": [],
        }
        for f in files:
            p = f.path.lower()
            if any(x in p for x in ("ui/", "frontend/", "components/", "pages/")):
                layers["ui"].append(f.path)
            elif any(x in p for x in ("routes", "endpoints", "controllers", "api/")):
                layers["api"].append(f.path)
            elif any(x in p for x in ("domain/", "models/", "services/")):
                layers["domain"].append(f.path)
            elif any(x in p for x in ("infra", "infrastructure", "repository", "db_")):
                layers["infra"].append(f.path)
            else:
                layers["other"].append(f.path)

        lines = [
            "graph TB",
            '    UI["前端/UI"]',
            '    API["API/接口层"]',
            '    Domain["领域/业务层"]',
            '    Infra["基础设施层"]',
            "    UI --> API",
            "    API --> Domain",
            "    Domain --> Infra",
        ]
        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.ARCHITECTURE,
            format=ArtifactFormat.MERMAID,
            title="架构图",
            content="\n".join(lines),
            meta={k: len(v) for k, v in layers.items()},
            created_at=datetime.now(),
        )

    def _data_flow(self, codebase_id: str, files: List[CodebaseFile], symbols: List[CodebaseSymbol]) -> CodebaseArtifact:
        lines = [
            "flowchart LR",
            '    User["用户"] --> UI["前端"]',
            '    UI --> API["API"]',
            '    API --> Service["服务层"]',
            '    Service --> DB["数据库/存储"]',
            '    Service --> Sandbox["沙箱执行"]',
        ]
        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.DATA_FLOW,
            format=ArtifactFormat.MERMAID,
            title="数据流向图",
            content="\n".join(lines),
            created_at=datetime.now(),
        )

    def _call_chain(
            self,
            codebase_id: str,
            files: List[CodebaseFile],
            symbols: List[CodebaseSymbol],
            edges: List[CodebaseEdge],
    ) -> CodebaseArtifact:
        sym_by_id = {s.id: s for s in symbols}
        path_by_file_id = {f.id: f.path for f in files}
        lines = ["graph LR"]
        seen_nodes: set[str] = set()
        seen_edges: set[str] = set()
        for edge in edges[:80]:
            src = sym_by_id.get(edge.src_symbol_id)
            if not src:
                continue
            src_node = f"S{src.id[:8]}"
            if src_node not in seen_nodes:
                lines.append(f'    {src_node}["{src.name}"]')
                seen_nodes.add(src_node)
            if edge.dst_symbol_id:
                dst = sym_by_id.get(edge.dst_symbol_id)
                if dst:
                    dst_node = f"S{dst.id[:8]}"
                    if dst_node not in seen_nodes:
                        lines.append(f'    {dst_node}["{dst.name}"]')
                        seen_nodes.add(dst_node)
                    edge_key = f"{src_node}->{dst_node}"
                    if edge_key not in seen_edges:
                        lines.append(f"    {src_node} --> {dst_node}")
                        seen_edges.add(edge_key)
            else:
                callee_node = f"C{edge.callee_name}"
                if callee_node not in seen_nodes:
                    lines.append(f'    {callee_node}["{edge.callee_name}"]')
                    seen_nodes.add(callee_node)
                edge_key = f"{src_node}->{callee_node}"
                if edge_key not in seen_edges:
                    lines.append(f"    {src_node} --> {callee_node}")
                    seen_edges.add(edge_key)

        if len(lines) == 1:
            lines.append('    Empty["暂无调用关系"]')

        node_locations: List[Dict[str, object]] = []
        seen_symbol_ids: set[str] = set()
        for edge in edges[:40]:
            for sym_id in (edge.src_symbol_id, edge.dst_symbol_id):
                if not sym_id or sym_id in seen_symbol_ids:
                    continue
                sym = sym_by_id.get(sym_id)
                if not sym:
                    continue
                path = path_by_file_id.get(sym.file_id, "")
                if not path:
                    continue
                seen_symbol_ids.add(sym_id)
                node_locations.append({
                    "symbol": sym.name,
                    "symbol_id": sym.id,
                    "path": path,
                    "line": sym.start_line,
                })

        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.CALL_CHAIN,
            format=ArtifactFormat.MERMAID,
            title="调用链图",
            content="\n".join(lines),
            meta={"node_locations": node_locations},
            created_at=datetime.now(),
        )

    def _flowchart(self, codebase_id: str, symbols: List[CodebaseSymbol]) -> CodebaseArtifact:
        funcs = [s for s in symbols if s.kind.value in ("function", "method")][:15]
        lines = ["flowchart TD", '    Start(["开始"])']
        prev = "Start"
        for i, sym in enumerate(funcs):
            node = f"F{i}"
            lines.append(f'    {node}["{sym.name}"]')
            lines.append(f"    {prev} --> {node}")
            prev = node
        lines.append(f"    {prev} --> End([结束])")
        return CodebaseArtifact(
            id=str(uuid.uuid4()),
            codebase_id=codebase_id,
            kind=ArtifactKind.FLOWCHART,
            format=ArtifactFormat.MERMAID,
            title="主要流程图",
            content="\n".join(lines),
            created_at=datetime.now(),
        )
