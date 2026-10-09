"""Small pure diagnostic for fixed OWNERS keyset page fixtures.

This checks a supplied page transcript, not an actual PostgreSQL read. In
particular, self-consistent omitted rows remain possible without an
independent same-snapshot count and digest of the original relation.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from scripts.execution_capacity.inventory_sql import OWNERS_PAGE_AFTER, OWNERS_PAGE_FIRST

_PROJECTION = frozenset(
    {
        "stream_type",
        "stream_id",
        "owner_scope_key",
        "source_entity_type",
        "source_entity_id",
        "correlation_id",
        "stream_version",
        "terminal",
    }
)
_MAX_PAGES = 33
_MAX_PAGE_SIZE = 128
_MAX_KEY_CHARS = 256
_MAX_ROW_TEXT_CHARS = 1024


@dataclass(frozen=True)
class OwnersPage:
    statement: str
    parameters: dict[str, Any]
    preflight_row_count: int
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class OwnersPageChainDiagnostic:
    observed_count: int
    last_key: tuple[str, str] | None
    eof_observed: bool
    independent_completeness_proven: bool = False


def verify_owners_page_chain(
    pages: tuple[OwnersPage, ...], *, declared_total_count: int | None = None
) -> OwnersPageChainDiagnostic:
    """Check fixed SQL, bindings, counts, keyset progression and explicit EOF.

    Python string ordering is only a fixture oracle; PostgreSQL collation and
    same-snapshot equivalence need a separate database proof. The optional
    declared total is supplied by the fixture and is not independent evidence.
    """
    if type(pages) is not tuple or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError("bounded pages tuple required")
    if declared_total_count is not None and (
        type(declared_total_count) is not int or declared_total_count < 0
    ):
        raise ValueError("invalid declared total count")

    last_key = None
    observed_count = 0
    page_size = None
    preceding_short = False
    eof = False
    for index, page in enumerate(pages):
        if (
            type(page) is not OwnersPage
            or type(page.parameters) is not dict
            or type(page.rows) is not tuple
        ):
            raise ValueError("invalid page fixture")
        if eof:
            raise ValueError("page follows EOF")
        params = page.parameters
        size = params.get("page_size")
        if type(size) is not int or not 1 <= size <= _MAX_PAGE_SIZE:
            raise ValueError("invalid diagnostic page size")
        if page_size is None:
            page_size = size
        elif size != page_size:
            raise ValueError("page size changed")
        expected_sql = OWNERS_PAGE_FIRST if index == 0 else OWNERS_PAGE_AFTER
        expected_params = (
            {"page_size": page_size}
            if index == 0
            else {"page_size": page_size, "after_type": last_key[0], "after_id": last_key[1]}
        )
        if (
            page.statement != expected_sql
            or params != expected_params
            or set(params) != set(expected_params)
        ):
            raise ValueError("fixed page SQL or cursor binding differs")
        count = page.preflight_row_count
        if type(count) is not int or count < 0 or count > page_size or count != len(page.rows):
            raise ValueError("page preflight count differs from rows")
        if preceding_short and count:
            raise ValueError("nonempty page follows a short page")
        if not count:
            if index != len(pages) - 1:
                raise ValueError("page follows EOF")
            eof = True
            continue
        for row in page.rows:
            if type(row) is not dict or set(row) != _PROJECTION:
                raise ValueError("OWNERS projection differs")
            text_chars = 0
            for value in row.values():
                if type(value) is str:
                    text_chars += len(value)
                    if text_chars > _MAX_ROW_TEXT_CHARS:
                        raise ValueError("OWNERS row text exceeds diagnostic bound")
                elif value is not None and type(value) not in (bool, UUID):
                    if type(value) is not int or value.bit_length() > 64:
                        raise ValueError("OWNERS row value exceeds diagnostic bound")
            key = (row["stream_type"], row["stream_id"])
            if any(type(part) is not str or not part or len(part) > _MAX_KEY_CHARS for part in key):
                raise ValueError("NULL or invalid OWNERS key")
            if last_key is not None and key <= last_key:
                raise ValueError("duplicate or descending OWNERS key")
            last_key = key
        observed_count += count
        preceding_short = count < page_size
    if not eof:
        raise ValueError("missing explicit empty EOF page")
    if declared_total_count is not None and observed_count != declared_total_count:
        raise ValueError("declared total count differs from pages")
    return OwnersPageChainDiagnostic(observed_count, last_key, True)
