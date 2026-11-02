import pytest

from app.application.execution.context_lookup import ContextLexicalIndex
from tests.app.application.execution.test_context_compiler import BOUNDARY, source


def test_exact_fact_after_initial_excerpt_is_ranked_and_bound_to_original_span():
    index = ContextLexicalIndex(BOUNDARY, byte_limit=100000)
    early = source(1, "seed").ref
    content = "ordinary evidence " * 700 + " ARCHIVE-Z19 amount=7341; do not renew."
    index.add(early, content)
    index.add(source(2, "other").ref, "Later ordinary evidence with amount=19.")
    matches, record = index.search("ARCHIVE-Z19 amount and prohibition", limit=2)
    assert matches[0][0] == early
    assert matches[0][1] > 8192
    assert "amount=7341; do not renew." in matches[0][2]
    offset, text = matches[0][1:]
    assert content.encode()[offset : offset + len(text.encode())].decode() == text
    assert record["index_revision"] == index.revision
    assert record["query"] == "ARCHIVE-Z19 amount and prohibition"
    assert record["matches"][0]["source"] == early.model_dump(mode="json")
    assert index.search(record["query"], limit=2) == (matches, record)


def test_chinese_query_and_utf8_offsets_recover_exact_constraint():
    index = ContextLexicalIndex(BOUNDARY, byte_limit=100000)
    original = "普通信息。" * 1500 + "采购限额是７３４１元，禁止自动续费。"
    index.add(source(1, "seed").ref, original)
    matches, _ = index.search("采购限额 自动续费 7341", limit=1)
    assert "采购限额是７３４１元，禁止自动续费。" in matches[0][2]
    offset, text = matches[0][1:]
    assert original.encode()[offset : offset + len(text.encode())].decode() == text


def test_index_revision_covers_catalog_boundary_and_all_original_bytes():
    ref = source(1, "seed").ref
    first = ContextLexicalIndex(BOUNDARY, byte_limit=100)
    changed = ContextLexicalIndex(BOUNDARY, byte_limit=100)
    later = ContextLexicalIndex(
        BOUNDARY.model_copy(update={"through_position": 1001}), byte_limit=100
    )
    first.add(ref, "early amount=1")
    changed.add(ref, "early amount=2")
    later.add(ref, "early amount=1")
    assert len({first.revision, changed.revision, later.revision}) == 3


def test_lookup_limits_and_scope_fail_closed():
    index = ContextLexicalIndex(BOUNDARY, byte_limit=10)
    ref = source(1, "seed").ref
    with pytest.raises(ValueError, match="CONTEXT_LOOKUP_BYTES_EXCEEDED"):
        index.add(ref, "x" * 11)
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_BOUNDARY_MISMATCH"):
        index.add(ref.model_copy(update={"scope_key": "team:foreign"}), "fact")
    index.add(ref, "fact")
    with pytest.raises(ValueError, match="CONTEXT_SOURCE_IDENTITY_CONFLICT"):
        index.add(ref, "fact")
    with pytest.raises(ValueError, match="CONTEXT_LOOKUP_QUERY_INVALID"):
        index.search("fact", limit=5)
    assert index.search("missing", limit=2)[0] == ()
    assert index.search("fact", limit=0)[0] == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "knowledge", "memory"])
async def test_original_evidence_spans_retain_authority_without_executable_roles(kind):
    from app.domain.runtime_policy.context import ContextPolicy
    from app.infrastructure.execution.context_original_lookup import OriginalLookup

    lookup = OriginalLookup(BOUNDARY, ContextPolicy())
    ref = source(1, "seed", kind=kind).ref
    original = "ordinary data " * 9000 + " ARCHIVE-Z19 exact=7341; do not renew."
    if kind == "knowledge":
        import json

        original = json.dumps({"query": "QUERY-ECHO-ONLY", "sources": [{"content": original}]})
    raw = original.encode()
    offsets = []

    async def read(offset, limit):
        offsets.append(offset)
        end = min(offset + limit, len(raw))
        return raw[offset:end].decode(), end if end < len(raw) else None, len(raw)

    await lookup.add_original(ref, read)
    matches, record = lookup.search("ARCHIVE-Z19 exact prohibition")
    assert len(offsets) > 1
    assert matches[0].ref == ref
    assert matches[0].protected
    assert matches[0].messages[0].role == "user"
    assert not matches[0].messages[0].tool_calls
    assert "exact=7341; do not renew." in matches[0].messages[0].content
    assert record["matches"][0]["offset"] > 8192
    if kind == "knowledge":
        assert lookup.search("QUERY-ECHO-ONLY")[0] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "following", "total"), [("abc", 2, 4), ("abc", None, 4), ("abc", 3, 2)]
)
async def test_original_lookup_rejects_gaps_truncation_and_wrong_lengths(value, following, total):
    from app.domain.runtime_policy.context import ContextPolicy
    from app.infrastructure.execution.context_original_lookup import OriginalLookup

    lookup = OriginalLookup(BOUNDARY, ContextPolicy())

    async def read(offset, limit):
        return value, following, total

    with pytest.raises(ValueError, match="CONTEXT_LOOKUP_SPAN_INVALID"):
        await lookup.add_original(source(1, "seed").ref, read)
