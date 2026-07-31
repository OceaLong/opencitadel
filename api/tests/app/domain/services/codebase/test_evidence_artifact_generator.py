#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.codebase import ArtifactKind, EdgeKind
from app.domain.services.codebase.artifact_generator import ArtifactGenerator
from app.domain.services.codebase.static_analyzer import StaticAnalyzer


def test_empty_evidence_does_not_generate_architecture_dataflow_or_flowchart():
    generator = ArtifactGenerator()

    result = generator.generate_all(
        "cbv1",
        files=[],
        symbols=[],
        edges=[],
        language_stats={},
    )

    assert {a.kind for a in result.artifacts} == set()
    assert result.unsupported_views == {
        ArtifactKind.ARCHITECTURE: "insufficient_evidence",
        ArtifactKind.DATA_FLOW: "unsupported",
        ArtifactKind.CALL_CHAIN: "insufficient_evidence",
        ArtifactKind.FLOWCHART: "unsupported",
    }


def test_call_chain_edges_have_source_evidence():
    analysis = StaticAnalyzer().analyze(
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
    generator = ArtifactGenerator()

    artifact = generator.generate_call_chain(analysis)

    assert artifact.content
    assert artifact.meta["edges"]
    assert all(edge["evidence_refs"] for edge in artifact.meta["edges"])
    assert all(
        edge["kind"] == EdgeKind.CALL.value
        for edge in artifact.meta["edges"]
    )


def test_function_list_is_never_serialized_as_flow():
    analysis = StaticAnalyzer().analyze(
        files={
            "src/main.py": (
                "def first():\n"
                "    return 1\n\n"
                "def second():\n"
                "    return 2\n"
            )
        },
        version_id="cbv1",
    )
    generator = ArtifactGenerator()

    result = generator.generate_all_from_analysis(analysis)

    assert ArtifactKind.FLOWCHART not in {a.kind for a in result.artifacts}
    assert result.unsupported_views[ArtifactKind.FLOWCHART] == "unsupported"
