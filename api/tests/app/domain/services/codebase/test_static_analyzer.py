#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.codebase.static_analyzer import StaticAnalyzer


def test_analyze_python_symbols_and_calls():
    content = '''
def foo():
    bar()

def bar():
    return 1
'''
    analyzer = StaticAnalyzer()
    result = analyzer.analyze_tree("cb-1", "/tmp", [("main.py", content)])
    assert len(result.files) == 1
    names = {s.name for s in result.symbols}
    assert "foo" in names
    assert "bar" in names
    assert len(result.edges) >= 1


def test_build_module_dir_artifact_via_generator():
    from app.domain.services.codebase.artifact_generator import ArtifactGenerator

    files = []
    symbols = []
    edges = []
    gen = ArtifactGenerator()
    artifacts = gen.generate_all("cb-1", "demo", files, symbols, edges, {"python": 1})
    kinds = {a.kind.value for a in artifacts}
    assert "architecture" in kinds
    assert "overview" in kinds
    assert "call_chain" in kinds
