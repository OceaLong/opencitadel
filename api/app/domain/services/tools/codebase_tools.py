#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Codebase retrieval tools for Ask/Agent modes."""
import logging
from typing import Callable, Optional

from app.domain.external.sandbox import Sandbox
from app.domain.models.codebase import ArtifactKind
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.codebase.vector_service import CodebaseVectorService
from app.domain.services.tools.base import BaseTool, tool
from app.domain.utils.sandbox_result import file_content

logger = logging.getLogger(__name__)


class CodebaseTool(BaseTool):
    name: str = "codebase"

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            codebase_id: str,
            sandbox: Sandbox,
            workspace_path: str = "/home/ubuntu/codebase",
    ) -> None:
        super().__init__()
        self._uow_factory = uow_factory
        self._codebase_id = codebase_id
        self._sandbox = sandbox
        self._workspace = workspace_path.rstrip("/")
        self._vector = CodebaseVectorService()

    @tool(
        name="semantic_search",
        description="语义搜索代码库，根据自然语言查询找到相关代码片段",
        parameters={
            "query": {"type": "string", "description": "搜索查询"},
            "limit": {"type": "integer", "description": "返回结果数量，默认5"},
        },
        required=["query"],
    )
    async def semantic_search(self, query: str, limit: int = 5) -> str:
        embedding = await self._vector.embed(query)
        async with self._uow_factory() as uow:
            results = await uow.codebase.search_chunks(self._codebase_id, embedding, limit=limit)
            files = {f.id: f for f in await uow.codebase.list_files(self._codebase_id)}
        lines = []
        for chunk, score in results:
            path = files[chunk.file_id].path if chunk.file_id and chunk.file_id in files else "?"
            lines.append(f"[score={score:.3f}] {path}\n{chunk.content[:800]}")
        return "\n\n---\n\n".join(lines) if lines else "未找到相关代码"

    @tool(
        name="find_symbol",
        description="按名称查找符号（函数/类/方法）",
        parameters={
            "name": {"type": "string", "description": "符号名称"},
        },
        required=["name"],
    )
    async def find_symbol(self, name: str) -> str:
        async with self._uow_factory() as uow:
            symbols = await uow.codebase.find_symbol_by_name(self._codebase_id, name)
            files = {f.id: f for f in await uow.codebase.list_files(self._codebase_id)}
        if not symbols:
            return f"未找到符号: {name}"
        lines = []
        for s in symbols:
            path = files[s.file_id].path if s.file_id in files else "?"
            lines.append(f"{s.name} ({s.kind.value}) @ {path}:{s.start_line}")
        return "\n".join(lines)

    @tool(
        name="find_references",
        description="查找符号的引用/调用关系",
        parameters={
            "name": {"type": "string", "description": "符号名称"},
        },
        required=["name"],
    )
    async def find_references(self, name: str) -> str:
        async with self._uow_factory() as uow:
            symbols = await uow.codebase.find_symbol_by_name(self._codebase_id, name)
            if not symbols:
                return f"未找到 {name} 的引用"
            sym_ids = {s.id for s in symbols}
            refs: list = []
            for sym_id in sym_ids:
                refs.extend(
                    await uow.codebase.list_edges(
                        self._codebase_id,
                        dst_symbol_id=sym_id,
                    ),
                )
            refs.extend(
                await uow.codebase.list_edges(
                    self._codebase_id,
                    callee_name=name,
                ),
            )
            seen = set()
            unique_refs = []
            for edge in refs:
                key = (edge.src_symbol_id, edge.dst_symbol_id, edge.callee_name)
                if key in seen:
                    continue
                seen.add(key)
                unique_refs.append(edge)
            if not unique_refs:
                return f"未找到 {name} 的引用"
            src_ids = [e.src_symbol_id for e in unique_refs if e.src_symbol_id]
            src_symbols = await uow.codebase.list_symbols_by_ids(self._codebase_id, src_ids)
            sym_by_id = {s.id: s for s in src_symbols}
            files = {f.id: f for f in await uow.codebase.list_files(self._codebase_id)}
        lines = []
        for e in unique_refs[:30]:
            src = sym_by_id.get(e.src_symbol_id)
            if src:
                path = files[src.file_id].path if src.file_id in files else "?"
                lines.append(f"{path}:{src.start_line} -> {name}")
        return "\n".join(lines)

    @tool(
        name="get_call_chain",
        description="获取符号的调用链（入站+出站）",
        parameters={
            "symbol_name": {"type": "string", "description": "符号名称"},
        },
        required=["symbol_name"],
    )
    async def get_call_chain(self, symbol_name: str) -> str:
        async with self._uow_factory() as uow:
            symbols = await uow.codebase.find_symbol_by_name(self._codebase_id, symbol_name)
            if not symbols:
                return f"未找到符号: {symbol_name}"
            sym = symbols[0]
            out_edges = await uow.codebase.list_edges(self._codebase_id, src_symbol_id=sym.id)
            in_edges = await uow.codebase.list_edges(self._codebase_id, dst_symbol_id=sym.id)
            related_ids = {
                *(e.dst_symbol_id for e in out_edges if e.dst_symbol_id),
                *(e.src_symbol_id for e in in_edges if e.src_symbol_id),
            }
            related_symbols = await uow.codebase.list_symbols_by_ids(
                self._codebase_id,
                list(related_ids),
            )
            sym_by_id = {s.id: s for s in related_symbols}
        lines = [f"## {symbol_name} 调用链", "### 调用 (outbound)"]
        for e in out_edges[:20]:
            callee = e.callee_name
            if e.dst_symbol_id and e.dst_symbol_id in sym_by_id:
                callee = sym_by_id[e.dst_symbol_id].name
            lines.append(f"  -> {callee}")
        lines.append("### 被调用 (inbound)")
        for e in in_edges[:20]:
            src = sym_by_id.get(e.src_symbol_id)
            if src:
                lines.append(f"  <- {src.name}")
        return "\n".join(lines)

    @tool(
        name="get_file_tree",
        description="获取代码库文件目录树概览",
        parameters={},
        required=[],
    )
    async def get_file_tree(self) -> str:
        async with self._uow_factory() as uow:
            files = await uow.codebase.list_files(self._codebase_id)
        dirs: dict = {}
        for f in files:
            parts = f.path.split("/")
            node = dirs
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node.setdefault("_files", []).append(parts[-1])
        import json
        return json.dumps(dirs, ensure_ascii=False, indent=2)[:8000]

    @tool(
        name="read_code",
        description="读取指定路径的源码，可指定行范围",
        parameters={
            "path": {"type": "string", "description": "相对文件路径"},
            "start_line": {"type": "integer", "description": "起始行号(1-based)"},
            "end_line": {"type": "integer", "description": "结束行号"},
        },
        required=["path"],
    )
    async def read_code(
            self,
            path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
    ) -> str:
        full_path = f"{self._workspace}/{path.lstrip('/')}"
        result = await self._sandbox.read_file(full_path, start_line=start_line, end_line=end_line)
        if not result.success:
            return f"读取失败: {result.message or path}"
        loc = f"{path}:{start_line or 1}"
        return f"```{path}\n# {loc}\n{file_content(result)}\n```"

    @tool(
        name="get_diagram",
        description="获取预生成的架构/数据流/调用链/流程图(Mermaid)",
        parameters={
            "kind": {
                "type": "string",
                "description": "图类型: architecture|data_flow|module_dir|flowchart|call_chain|overview",
            },
        },
        required=["kind"],
    )
    async def get_diagram(self, kind: str) -> str:
        try:
            artifact_kind = ArtifactKind(kind)
        except ValueError:
            return f"未知图类型: {kind}"
        async with self._uow_factory() as uow:
            artifacts = await uow.codebase.list_artifacts(self._codebase_id, kind=artifact_kind)
        if not artifacts:
            return f"暂无 {kind} 图表"
        a = artifacts[-1]
        if a.format.value == "mermaid":
            return f"```mermaid\n{a.content}\n```"
        return a.content
