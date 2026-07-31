#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.codebase import EdgeKind, SymbolKind
from app.domain.services.codebase.static_analyzer import StaticAnalyzer


def test_same_named_methods_are_not_deduplicated():
    analyzer = StaticAnalyzer()

    result = analyzer.analyze(
        files={
            "a.ts": (
                "class A { run() { return 1 } }\n"
                "class B { run() { return 2 } }\n"
            ),
        },
    )

    assert {
        s.qualified_name
        for s in result.symbols
        if s.name == "run"
    } == {"A.run", "B.run"}


def test_ambiguous_call_is_not_bound_to_first_symbol():
    analyzer = StaticAnalyzer()

    result = analyzer.analyze(
        files={
            "a.py": "def work():\n    pass\n",
            "b.py": "def work():\n    pass\n",
            "c.py": "def caller():\n    work()\n",
        },
    )

    edge = next(e for e in result.edges if e.callee_name == "work")
    assert edge.dst_symbol_id is None
    assert edge.resolution == "ambiguous"
    assert edge.kind is EdgeKind.CALL


def test_non_python_symbol_range_contains_body():
    analyzer = StaticAnalyzer()

    symbol = analyzer.analyze(
        files={"a.ts": "function f() {\n  return 1\n}\n"},
    ).symbols[0]

    assert symbol.name == "f"
    assert (symbol.start_line, symbol.end_line) == (1, 3)
    assert symbol.parser in {"tree_sitter", "regex"}
    assert symbol.confidence > 0


def test_python_symbols_have_qualified_names_parser_and_confidence():
    analyzer = StaticAnalyzer()

    result = analyzer.analyze(
        files={
            "pkg/service.py": (
                "class UserService:\n"
                "    def create_user(self, name):\n"
                "        return name\n"
            )
        },
    )

    method = next(s for s in result.symbols if s.name == "create_user")
    assert method.kind is SymbolKind.METHOD
    assert method.qualified_name.endswith("UserService.create_user")
    assert method.parser == "python_ast"
    assert method.confidence >= 0.9
    assert (method.start_line, method.end_line) == (2, 3)


def test_unique_call_resolves_with_evidence_and_version():
    analyzer = StaticAnalyzer()

    result = analyzer.analyze(
        files={
            "src/main.py": (
                "def work():\n"
                "    return 1\n\n"
                "def caller():\n"
                "    return work()\n"
            )
        },
        version_id="cbv1",
    )

    edge = next(e for e in result.edges if e.callee_name == "work")
    assert edge.dst_symbol_id is not None
    assert edge.resolution == "resolved"
    assert edge.confidence > 0
    assert edge.evidence
    ref = edge.evidence[0]
    assert ref.version_id == "cbv1"
    assert ref.path == "src/main.py"
    assert ref.start_line == 5
    assert ref.end_line == 5
    assert ref.analyzer
