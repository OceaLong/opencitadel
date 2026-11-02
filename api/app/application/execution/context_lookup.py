"""Deterministic lexical lookup over authorized, immutable original spans."""

import re
import unicodedata
from collections import Counter, defaultdict
from fractions import Fraction

from app.domain.execution.context_models import context_digest


def terms(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = set(re.findall(r"[^\W_]+", normalized))
    for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
        words.discard(sequence)
        words.update(sequence[index : index + 2] for index in range(max(1, len(sequence) - 1)))
    return words


class ContextLexicalIndex:
    """Rebuilt from the accepted catalog; its revision binds every indexed byte.

    Postings and ranking are local and deterministic. No embedding or model call
    is made, and the bounded selected spans retain their original byte offsets.
    """

    algorithm = "original-lexical-v2"

    def __init__(self, boundary, *, byte_limit):
        self.boundary = boundary
        self.byte_limit = byte_limit
        self.byte_count = 0
        self.documents = []
        self.spans = []
        self.postings = defaultdict(set)

    def add(self, ref, content, *, searchable_ranges=None):
        raw = content.encode()
        if self.byte_count + len(raw) > self.byte_limit or len(self.documents) >= 4096:
            raise ValueError("CONTEXT_LOOKUP_BYTES_EXCEEDED")
        if any(item[0] == ref.source_id for item in self.documents):
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        if ref.scope_key != self.boundary.scope_key or (
            ref.formal_position > self.boundary.through_position
        ):
            raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        ranges = ((0, len(raw)),) if searchable_ranges is None else tuple(searchable_ranges)
        previous = 0
        for start, end in ranges:
            if not (type(start) is type(end) is int and previous <= start <= end <= len(raw)):
                raise ValueError("CONTEXT_LOOKUP_SPAN_INVALID")
            try:
                raw[start:end].decode()
            except UnicodeDecodeError as error:
                raise ValueError("CONTEXT_LOOKUP_SPAN_INVALID") from error
            previous = end
        self.byte_count += len(raw)
        self.documents.append((ref.source_id, ref.digest, context_digest(content), ranges))
        for start, stop in ranges:
            self._add_spans(ref, raw, start, stop)

    def _add_spans(self, ref, raw, start, stop):
        while start < stop:
            end = min(stop, start + 2048)
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            value = raw[start:end].decode()
            ordinal = len(self.spans)
            self.spans.append((ref, start, value))
            for term in terms(value):
                self.postings[term].add(ordinal)
            if end == stop:
                break
            # Overlap retains a phrase or exact identifier across a span boundary.
            start = end - 256
            while raw[start] & 0xC0 == 0x80:
                start += 1

    @property
    def revision(self):
        return context_digest(
            {
                "algorithm": self.algorithm,
                "boundary": self.boundary.model_dump(mode="json"),
                "documents": self.documents,
            }
        )

    def search(self, query, *, limit):
        if not 0 <= limit <= 4 or len(query.encode()) > 262144:
            raise ValueError("CONTEXT_LOOKUP_QUERY_INVALID")
        scores = Counter()
        for term in sorted(terms(query)):
            hits = self.postings.get(term, ())
            for ordinal in hits:
                # Exact rational scores keep ordering stable across runtimes.
                scores[ordinal] += Fraction(1, len(hits))
        selected, identities = [], set()
        for ordinal in sorted(scores, key=lambda value: (-scores[value], value)):
            if len(selected) >= limit:
                break
            ref, offset, value = self.spans[ordinal]
            if ref.source_id in identities:
                continue
            identities.add(ref.source_id)
            selected.append((ref, offset, value))
        record = {
            "algorithm": self.algorithm,
            "query": query,
            "index_revision": self.revision,
            "indexed_sources": len(self.documents),
            "indexed_bytes": self.byte_count,
            "matches": [
                {
                    "source": ref.model_dump(mode="json"),
                    "offset": offset,
                    "byte_length": len(value.encode()),
                    "content_digest": context_digest(value),
                }
                for ref, offset, value in selected
            ],
        }
        return tuple(selected), record
