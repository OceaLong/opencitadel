#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.codebase import (
    CodebaseEdge,
    CodebaseFile,
    CodebaseSymbol,
    EdgeKind,
    SymbolKind,
)
from app.domain.services.codebase.artifact_generator import ArtifactGenerator


def test_call_chain_node_locations_include_path_and_dedupe():
    file_a = CodebaseFile(
        id="file-a",
        codebase_id="cb-1",
        path="src/captcha.py",
        language="python",
    )
    file_b = CodebaseFile(
        id="file-b",
        codebase_id="cb-1",
        path="src/gateway.py",
        language="python",
    )
    foo = CodebaseSymbol(
        id="sym-foo",
        codebase_id="cb-1",
        file_id="file-a",
        name="checkCaptcha",
        kind=SymbolKind.FUNCTION,
        start_line=100,
    )
    bar = CodebaseSymbol(
        id="sym-bar",
        codebase_id="cb-1",
        file_id="file-b",
        name="routerFunction",
        kind=SymbolKind.FUNCTION,
        start_line=25,
    )
    edges = [
        CodebaseEdge(
            codebase_id="cb-1",
            src_symbol_id="sym-foo",
            dst_symbol_id="sym-bar",
            kind=EdgeKind.CALL,
        ),
        CodebaseEdge(
            codebase_id="cb-1",
            src_symbol_id="sym-foo",
            dst_symbol_id="sym-bar",
            kind=EdgeKind.CALL,
        ),
    ]

    gen = ArtifactGenerator()
    artifacts = gen.generate_all(
        "cb-1",
        "demo",
        [file_a, file_b],
        [foo, bar],
        edges,
        {"python": 2},
    )
    call_chain = next(a for a in artifacts if a.kind.value == "call_chain")
    locations = call_chain.meta["node_locations"]

    assert len(locations) == 2
    by_symbol = {loc["symbol"]: loc for loc in locations}
    assert by_symbol["checkCaptcha"] == {
        "symbol": "checkCaptcha",
        "symbol_id": "sym-foo",
        "path": "src/captcha.py",
        "line": 100,
    }
    assert by_symbol["routerFunction"] == {
        "symbol": "routerFunction",
        "symbol_id": "sym-bar",
        "path": "src/gateway.py",
        "line": 25,
    }
