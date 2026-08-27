"""Python AST parser adapter for codebase static analysis."""

from __future__ import annotations

import ast
from pathlib import PurePosixPath

from app.domain.models.codebase import SymbolKind
from app.domain.services.codebase.parsers.base import (
    ParsedCallSite,
    ParsedFile,
    ParsedSymbol,
    SourceRange,
)


class PythonParser:
    parser_name = "python_ast"
    confidence = 0.95

    def parse(self, path: str, content: str) -> ParsedFile:
        tree = ast.parse(content)
        content.splitlines()
        module_name = self._module_name(path)
        symbols: list[ParsedSymbol] = []
        calls: list[ParsedCallSite] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.scope: list[str] = [module_name] if module_name else []
                self.class_depth = 0

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                qualified = ".".join([*self.scope, node.name])
                symbols.append(
                    ParsedSymbol(
                        name=node.name,
                        qualified_name=qualified,
                        kind=SymbolKind.CLASS,
                        signature=f"class {node.name}",
                        range=SourceRange(
                            node.lineno,
                            getattr(node, "end_lineno", node.lineno) or node.lineno,
                        ),
                        parser=PythonParser.parser_name,
                        confidence=PythonParser.confidence,
                        parent_qualified_name=".".join(self.scope) or None,
                    )
                )
                self.scope.append(node.name)
                self.class_depth += 1
                self.generic_visit(node)
                self.class_depth -= 1
                self.scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

            def _visit_function(self, node: ast.AST) -> None:
                name = getattr(node, "name", "")
                qualified = ".".join([*self.scope, name])
                args = getattr(getattr(node, "args", None), "args", [])
                arg_names = [getattr(arg, "arg", "") for arg in args]
                kind = SymbolKind.METHOD if self.class_depth > 0 else SymbolKind.FUNCTION
                symbols.append(
                    ParsedSymbol(
                        name=name,
                        qualified_name=qualified,
                        kind=kind,
                        signature=f"def {name}({', '.join(arg_names)})",
                        range=SourceRange(
                            getattr(node, "lineno", 0),
                            getattr(node, "end_lineno", getattr(node, "lineno", 0))
                            or getattr(node, "lineno", 0),
                        ),
                        parser=PythonParser.parser_name,
                        confidence=PythonParser.confidence,
                        parent_qualified_name=".".join(self.scope) or None,
                    )
                )
                for call in ast.walk(node):
                    if not isinstance(call, ast.Call):
                        continue
                    callee = PythonParser._call_name(call.func)
                    if not callee or callee == name:
                        continue
                    calls.append(
                        ParsedCallSite(
                            caller_qualified_name=qualified,
                            callee_name=callee,
                            line=getattr(call, "lineno", getattr(node, "lineno", 0)),
                            parser=PythonParser.parser_name,
                            confidence=0.85,
                        )
                    )
                self.scope.append(name)
                self.generic_visit(node)
                self.scope.pop()

        Visitor().visit(tree)
        return ParsedFile(symbols=symbols, calls=calls)

    @staticmethod
    def _module_name(path: str) -> str:
        pure = PurePosixPath(path)
        without_suffix = pure.with_suffix("")
        parts = [part for part in without_suffix.parts if part not in {".", ""}]
        return ".".join(parts)

    @staticmethod
    def _call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None
