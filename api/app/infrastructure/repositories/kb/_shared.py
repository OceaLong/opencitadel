"""Shared helpers for knowledge repository mixins."""

import base64
import binascii
import json
import unicodedata

from sqlalchemy import text

from app.domain.models.knowledge_base import KnowledgeChunk

_CHUNK_INSERT_BATCH_SIZE = 500


def _is_cursor_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _encode_document_cursor(
    *,
    kb_id: str,
    version_id: str,
    doc_id: str,
    document_revision_id: str,
    page_no: int | None,
    chunk: KnowledgeChunk,
) -> str:
    payload = {
        "kb": kb_id,
        "version": version_id,
        "document": doc_id,
        "revision": document_revision_id,
        "page": page_no,
        "key": [chunk.page_no, chunk.ordinal, chunk.id],
    }
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_document_cursor(
    cursor: str,
    *,
    kb_id: str,
    version_id: str,
    doc_id: str,
    document_revision_id: str,
    page_no: int | None,
) -> tuple[int | None, int, str]:
    try:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid document cursor") from exc
    if not isinstance(payload, dict):
        raise TypeError("invalid document cursor")
    if (
        payload.get("kb") != kb_id
        or payload.get("version") != version_id
        or payload.get("document") != doc_id
        or payload.get("revision") != document_revision_id
        or payload.get("page") != page_no
    ):
        raise ValueError("document cursor does not match requested source")
    key = payload.get("key")
    if not isinstance(key, list) or len(key) != 3:
        raise ValueError("invalid document cursor key")
    key_page, key_ordinal, key_id = key
    if (
        (key_page is not None and (not _is_cursor_int(key_page) or key_page < 1))
        or not _is_cursor_int(key_ordinal)
        or key_ordinal < 0
        or not isinstance(key_id, str)
        or not key_id
    ):
        raise ValueError("invalid document cursor key")
    if page_no is not None and key_page != page_no:
        raise ValueError("document cursor key does not match requested page filter")
    return key_page, key_ordinal, key_id


_VERSIONED_VECTOR_SEARCH_SQL = """
SELECT c.id, c.kb_id, c.doc_id, c.version_id,
       c.parent_id, c.level, c.content,
       c.page_no, c.heading_path, c.ordinal,
       d.title, d.source_type, d.source_ref, d.mime, d.file_id,
       d.page_count, d.status, d.error, d.warning,
       d.created_at, d.updated_at,
       manifest.document_revision_id,
       1 - (c.embedding <=> CAST(:query AS vector)) AS score
FROM knowledge_chunks c
JOIN knowledge_base_versions version
  ON version.id = c.version_id
 AND version.knowledge_base_id = c.kb_id
 AND version.state IN ('ready', 'degraded')
 AND version.published_at IS NOT NULL
JOIN knowledge_base_version_documents manifest
  ON manifest.version_id = c.version_id
 AND manifest.knowledge_base_id = c.kb_id
 AND manifest.document_id = c.doc_id
 AND manifest.state = 'indexed'
JOIN knowledge_document_revisions revision
  ON revision.id = manifest.document_revision_id
 AND revision.document_id = manifest.document_id
 AND revision.state = 'indexed'
JOIN knowledge_documents d
  ON d.id = c.doc_id
 AND d.kb_id = c.kb_id
WHERE c.kb_id = :kb_id
  AND c.version_id = :version_id
  AND c.level = 'child'
  AND c.embedding IS NOT NULL
ORDER BY c.embedding <=> CAST(:query AS vector)
LIMIT :limit
""".strip()


def build_versioned_vector_search_statement(
    *,
    explain: bool = False,
):
    """Single production-owned SQL shape used by retrieval and ANN gate."""
    prefix = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)\n" if explain else ""
    return text(prefix + _VERSIONED_VECTOR_SEARCH_SQL)


def _parse_vector_text(value: object) -> list[float]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if not raw.strip():
        return []
    return [float(item) for item in raw.split(",")]


def _normalize_graph_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.strip().casefold().split())
