"""Small pure OWNERS page fixtures; they do not qualify database pagination."""

from dataclasses import replace

import pytest
from scripts.execution_capacity.inventory_sql import OWNERS, OWNERS_PAGE_AFTER, OWNERS_PAGE_FIRST
from scripts.execution_capacity.observer_sql_scope import fixed_texts
from scripts.execution_capacity.owners_page_chain import OwnersPage, verify_owners_page_chain


def _row(stream_id, *, stream_type="run", source_entity_id=None):
    return {
        "stream_type": stream_type,
        "stream_id": stream_id,
        "owner_scope_key": "scope",
        "source_entity_type": None,
        "source_entity_id": source_entity_id,
        "correlation_id": None,
        "stream_version": None,
        "terminal": None,
    }


def _page(rows, *, after=None, size=2, count=None):
    params = {"page_size": size}
    if after is not None:
        params.update(after_type=after[0], after_id=after[1])
    return OwnersPage(
        OWNERS_PAGE_FIRST if after is None else OWNERS_PAGE_AFTER,
        params,
        len(rows) if count is None else count,
        tuple(rows),
    )


def _valid_pages():
    return (
        _page((_row("a"), _row("b"))),
        _page((_row("c", source_entity_id=None),), after=("run", "b")),
        _page((), after=("run", "c")),
    )


def test_fixed_templates_preserve_original_text_projection_join_and_order():
    original = """SELECT s.stream_type,s.stream_id,s.owner_scope_key,p.source_entity_type,p.source_entity_id,p.correlation_id,
 p.stream_version,p.terminal FROM execution_stream_owners s LEFT JOIN execution_run_projection p
 ON s.stream_type='run' AND p.run_id::text=s.stream_id ORDER BY s.stream_type,s.stream_id"""
    assert original == OWNERS
    assert original + " LIMIT :page_size" == OWNERS_PAGE_FIRST
    assert (
        original.replace(
            " ORDER BY s.stream_type,s.stream_id",
            " WHERE (s.stream_type,s.stream_id) > (:after_type,:after_id)"
            " ORDER BY s.stream_type,s.stream_id",
        )
        + " LIMIT :page_size"
        == OWNERS_PAGE_AFTER
    )
    assert {OWNERS, OWNERS_PAGE_FIRST, OWNERS_PAGE_AFTER} <= fixed_texts()
    assert OWNERS_PAGE_AFTER + " OFFSET 1" not in fixed_texts()


def test_valid_chain_uses_actual_last_key_and_explicit_empty_eof():
    result = verify_owners_page_chain(_valid_pages(), declared_total_count=3)
    assert result.observed_count == 3
    assert result.last_key == ("run", "c")
    assert result.eof_observed is True
    assert result.independent_completeness_proven is False
    empty = verify_owners_page_chain((_page(()),), declared_total_count=0)
    assert empty.last_key is None
    assert empty.eof_observed is True


@pytest.mark.parametrize(
    ("pages", "error"),
    [
        (_valid_pages()[:-1], "missing explicit empty EOF"),
        ((*_valid_pages(), _page((), after=("run", "c"))), "page follows EOF"),
        (
            (_valid_pages()[0], _page((_row("c"),), after=("run", "a")), _valid_pages()[2]),
            "cursor binding",
        ),
        (
            (
                _page((_row("a"),)),
                _page((_row("b"),), after=("run", "a")),
                _page((), after=("run", "b")),
            ),
            "nonempty page follows a short page",
        ),
        (
            (_page((_row("a"), _row("a"))), _page((), after=("run", "a"))),
            "duplicate or descending",
        ),
        (
            (_page((_row("b"), _row("a"))), _page((), after=("run", "a"))),
            "duplicate or descending",
        ),
        (
            (_page((_row("a"),), count=2), _page((), after=("run", "a"))),
            "preflight count",
        ),
    ],
)
def test_tampered_chain_is_rejected(pages, error):
    with pytest.raises(ValueError, match=error):
        verify_owners_page_chain(pages)


def test_untrusted_sql_parameters_projection_and_counts_are_rejected():
    pages = _valid_pages()
    bad_sql = replace(pages[0], statement=OWNERS_PAGE_FIRST + " OFFSET 1")
    bad_params = replace(pages[1], parameters={**pages[1].parameters, "extra": 1})
    bad_null_key = replace(pages[0], rows=(_row(None), _row("b")))
    bad_projection = replace(pages[0], rows=({**_row("a"), "extra": None}, _row("b")))
    bad_long_key = replace(pages[0], rows=(_row("a" * 257), _row("b")))
    bad_long_value = replace(
        pages[0], rows=({**_row("a"), "owner_scope_key": "x" * 1025}, _row("b"))
    )
    bad_integer = replace(pages[0], rows=({**_row("a"), "stream_version": 1 << 64}, _row("b")))
    for changed, error in (
        (bad_sql, "fixed page SQL"),
        (bad_params, "cursor binding"),
        (bad_null_key, "OWNERS key"),
        (bad_projection, "projection"),
        (bad_long_key, "OWNERS key"),
        (bad_long_value, "diagnostic bound"),
        (bad_integer, "diagnostic bound"),
    ):
        with pytest.raises(ValueError, match=error):
            verify_owners_page_chain((changed, *pages[1:]))
    with pytest.raises(ValueError, match="declared total count"):
        verify_owners_page_chain(pages, declared_total_count=2)
    with pytest.raises(ValueError, match="declared total count"):
        verify_owners_page_chain(pages, declared_total_count=True)


def test_self_consistent_omission_remains_unproven_without_independent_global_proof():
    # The fixture claims its own count. No pure page-chain check can know that
    # an unseen key "b" belongs between "a" and "c" in the database relation.
    omitted = (_page((_row("a"), _row("c"))), _page((), after=("run", "c")))
    result = verify_owners_page_chain(omitted, declared_total_count=2)
    assert result.observed_count == 2
    assert result.independent_completeness_proven is False


def test_diagnostic_bounds_and_page_size_are_closed():
    with pytest.raises(ValueError, match="page size"):
        verify_owners_page_chain((_page((), size=129),))
    with pytest.raises(ValueError, match="page size changed"):
        verify_owners_page_chain(
            (_page((_row("a"), _row("b"))), _page((), after=("run", "b"), size=3))
        )
    with pytest.raises(ValueError, match="bounded pages"):
        verify_owners_page_chain(tuple(_page(()) for _ in range(34)))
