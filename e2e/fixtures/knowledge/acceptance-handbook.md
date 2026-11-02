# OpenCitadel Acceptance Handbook

## Deterministic operational fact

The Citadel verification beacon is cobalt-17 and rotates every 37 minutes.
This sentence is the canonical acceptance fact and must remain searchable from
the immutable knowledge-base version that contains it.

## Capability disclosure rule

When an optional index cannot be built, the product must name the unavailable
capability and its stable degradation reason. It must never present a partial
index as complete. Basic installations explicitly use keyword search with
optional vector and graph indexes disabled. When another document cannot be
parsed, the published version reports `DOCUMENT_PARTIAL` while this fact remains
searchable and its source can still be read.
