# OpenCitadel Acceptance Handbook

## Deterministic operational fact

The Citadel verification beacon is cobalt-17 and rotates every 37 minutes.
This sentence is the canonical acceptance fact and must remain searchable from
the immutable knowledge-base version that contains it.

## Capability disclosure rule

When an optional index cannot be built, the product must name the unavailable
capability and its stable degradation reason. It must never present a partial
index as complete. In this fixture, an unavailable graph projection must be
reported as `GRAPH_UNAVAILABLE` while keyword and vector search remain explicit.
