"""Canonical F06 citation metadata survives exact recorded retrieval, not replacements."""

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.dto.execution_view import CitationReference
from app.application.evaluation.recording_worker import RecordingWorker
from app.application.evaluation.replay_runtime import ReplayRuntime
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import canonical
from app.domain.models.knowledge_citation import KnowledgeCitation


async def generated(
    *, replacements=None, output_redacted=False, citation_overrides=None, result_query="beacon"
):
    citation = KnowledgeCitation(
        knowledge_base_id=str(uuid4()),
        version_id=str(uuid4()),
        document_revision_id=str(uuid4()),
        doc_id=str(uuid4()),
        chunk_id=str(uuid4()),
    )
    retrieval = SimpleNamespace(
        step_id="retrieval",
        activity_id=uuid4(),
        parent_step_id=None,
        status="completed",
        citation_refs=[
            CitationReference(
                **{
                    "citation_id": "fixed",
                    "availability": "available",
                    **citation.model_dump(),
                    **(citation_overrides or {}),
                }
            )
        ],
    )
    model = SimpleNamespace(
        step_id="model", activity_id=uuid4(), parent_step_id=None, status="completed"
    )
    identities = {
        (s.step_id, phase): uuid4() for s in (retrieval, model) for phase in ("input", "output")
    }
    objects = {}
    # Result-body citation-like data is deliberately different from trusted F06 metadata.
    forged = {**citation.model_dump(), "chunk_id": "forged-body-chunk"}
    result = {
        "query": result_query,
        "sources": [{"kind": "knowledge_base", "result": {"citations": [forged]}}],
    }

    class Source:
        async def steps(self, *args):
            return "signed-fixed-formal-cut", [retrieval, model]

        async def complete(self, scope, run, step, at, phase):
            assert at == "signed-fixed-formal-cut"
            value = (
                {"context": {"message": "beacon"}}
                if phase == "input"
                else {
                    "kind": "retrieval" if step == "retrieval" else "model",
                    "message": {"content": json.dumps(result)},
                }
            )
            return value, output_redacted if phase == "output" else False, identities[(step, phase)]

    class Repo:
        async def content_identity(self, scope, run, identity):
            return hashlib.sha256(str(identity).encode()).hexdigest()

        async def captured(self, *args):
            return {"fingerprint": "fixed-catalog", "contracts": []}

    class Dataset:
        async def authorize(self, *args, **kwargs):
            pass

    @asynccontextmanager
    async def uow(*args):
        yield SimpleNamespace(evaluation_dataset=Dataset(), evaluation_recording=Repo())

    class Lifecycle:
        async def put(self, *args, body, **kwargs):
            identity = uuid4()
            objects[str(identity)] = body
            return identity, hashlib.sha256(body).hexdigest(), len(body)

    service = SimpleNamespace(source=Source(), auth=lambda *args: None, uow_factory=uow)
    manifest = await RecordingWorker(service, Lifecycle())._generate(
        "scope",
        "principal",
        {
            "id": uuid4(),
            "source_run_id": uuid4(),
            "selection": [
                {
                    "tool": "__retrieval__",
                    "allowed_fields": ["query", "sources"],
                    "replacements": replacements or {},
                }
            ],
        },
        uuid4(),
    )
    return manifest, objects, citation


@pytest.mark.asyncio
async def test_recording_binds_original_canonical_citations_and_rejects_source_replacement():
    manifest, _, citation = await generated()
    metadata = manifest.slots[0].citation_evidence
    assert metadata.citations == (citation,)
    assert metadata.source_at == "signed-fixed-formal-cut"
    assert any(
        p.resource_id == str(metadata.output_content_id)
        and p.resource_version == metadata.output_digest
        for p in manifest.pins
    )
    changed = manifest.model_dump(mode="json")
    changed["slots"][0]["citation_evidence"]["citations"][0]["chunk_id"] = "changed"
    assert (
        hashlib.sha256(canonical(changed)).digest()
        != hashlib.sha256(canonical(manifest.model_dump(mode="json"))).digest()
    )
    with pytest.raises(ReplayMismatch, match="recording_citations_replacement_forbidden"):
        await generated(replacements={"sources": []})


@pytest.mark.asyncio
async def test_sanitized_source_preserves_canonical_metadata_without_recording_redacted_fields():
    manifest, _, citation = await generated(output_redacted=True)
    assert manifest.slots[0].citation_evidence.citations == (citation,)
    with pytest.raises(ReplayMismatch, match="replacement_requires_redaction"):
        await generated(output_redacted=True, result_query="[redacted]")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [{"availability": "unavailable"}, {"availability": "unknown"}, {"knowledge_base_id": None}],
)
async def test_sanitized_source_still_rejects_unavailable_or_unowned_canonical_citations(overrides):
    with pytest.raises(ReplayMismatch, match="source_citations_unavailable"):
        await generated(output_redacted=True, citation_overrides=overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "body_tamper", "missing_pin", "revoked", "legacy"])
async def test_exact_replay_restores_only_bound_current_canonical_citations(fault):
    manifest, objects, citation = await generated()
    if fault == "missing_pin":
        manifest = manifest.model_copy(update={"pins": ()})
    if fault == "legacy":
        manifest = manifest.model_copy(
            update={"slots": (manifest.slots[0].model_copy(update={"citation_evidence": None}),)}
        )
    slot = manifest.slots[0]
    ledger, captured = {}, []

    class Repo:
        async def lock_call(self, *args):
            pass

        async def consumed(self, *args):
            return ledger.get("call")

        async def object(self, *args):
            return {
                "storage_key": str(slot.object_id),
                "digest": slot.result_digest,
                "size_bytes": slot.result_bytes,
            }

        async def consume(self, *args):
            ledger["call"] = {
                "slot_id": slot.id,
                "version_id": manifest.id,
                "match_key": slot.match_key,
            }

    class Authority:
        @asynccontextmanager
        async def open(self, *args):
            if fault == "revoked":
                raise ReplayMismatch("recording_authority_revoked")

            async def commit():
                pass

            yield SimpleNamespace(
                manifest=manifest, repo=Repo(), scope="scope", uow=SimpleNamespace(commit=commit)
            )

        async def approve(self, *args):
            pass

    class Objects:
        async def get_bytes(self, key):
            return b"tampered" if fault == "body_tamper" else objects[key]

    async def record_citations(items):
        captured.extend(items)

    context = SimpleNamespace(
        activity_id=uuid4(), run=SimpleNamespace(run_id=uuid4()), record_citations=record_citations
    )
    runtime = ReplayRuntime(Authority(), Objects(), None)
    if fault in {"body_tamper", "missing_pin", "revoked"}:
        with pytest.raises(ReplayMismatch):
            await runtime.retrieval(SimpleNamespace(), context, "beacon")
        assert captured == []
    else:
        value = await runtime.retrieval(SimpleNamespace(), context, "beacon")
        assert value["sources"][0]["result"]["citations"][0]["chunk_id"] == "forged-body-chunk"
        assert captured == ([] if fault == "legacy" else [citation])
