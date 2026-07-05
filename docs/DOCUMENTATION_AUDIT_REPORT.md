[简体中文](DOCUMENTATION_AUDIT_REPORT.zh-CN.md)

# Documentation Audit Report

**Date:** 2026-07-06  
**Code baseline:** Current working tree (including uncommitted ingestion/OCR/upload changes)  
**Scope:** All Markdown docs, module READMEs, bilingual pairs, architecture diagrams, consistency with implementation

## Executive summary

The repository already had a mature documentation system (~80 Markdown files, EN/zh-CN pairs, `DOCUMENTATION_INVENTORY`, `check-docs.sh`). This audit aligned docs with recent codebase/knowledge ingestion work and filled structural gaps (nginx module README, KB ingestion architecture doc, upload limit matrix).

**Result:** Documentation checks pass (`./scripts/check-docs.sh`).

## What was added

| Document | Purpose |
|----------|---------|
| [architecture/knowledge-base-ingestion.md](architecture/knowledge-base-ingestion.md) (+ zh-CN) | KB parse, OCR `vision_llm`, GraphRAG, vector degraded, non-recoverable failures, reconcile |
| [nginx/README.md](../nginx/README.md) (+ zh-CN) | Gateway routing, SSE/WS, dynamic DNS, upload ceiling |

## What was updated

| Area | Changes |
|------|---------|
| [DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md) (+ zh-CN) | Fixed `technical-decisions` diagram field; added KB ingestion + nginx rows; refreshed stale risk |
| [architecture/overview.md](architecture/overview.md) (+ zh-CN) | Ingestion task types table; KB reconcile in Worker duties |
| [architecture/events.md](architecture/events.md) (+ zh-CN) | Ingestion `step` ids; `DOCUMENT_PARSE_FAILED` |
| [architecture/task-recovery.md](architecture/task-recovery.md) (+ zh-CN) | Agent vs KB ingest recovery boundaries |
| [architecture/config-source-governance.md](architecture/config-source-governance.md) (+ zh-CN) | Storage provider matrix; cross-layer upload limits |
| [architecture/codebase-reindex.md](architecture/codebase-reindex.md) (+ zh-CN) | 200 MB ZIP limit; `sandbox_result.py` |
| [operations/deployment.md](operations/deployment.md) (+ zh-CN) | Upload size limits section |
| [tutorials/02-internal-knowledge-base.md](tutorials/02-internal-knowledge-base.md) (+ zh-CN) | OCR, 50 MB doc limit, links to architecture doc |
| [docs/README.md](README.md) (+ zh-CN) | Index entries for new docs |
| [ui/README.md](../ui/README.md) (+ zh-CN) | `constants.ts`, `file.ts` |
| [MAINTENANCE_CHECKLIST.md](MAINTENANCE_CHECKLIST.md) (+ zh-CN) | Ingestion + upload limit maintenance rules |
| [scripts/check-docs.sh](../scripts/check-docs.sh) | nginx pair, KB ingestion index, inventory diagram guard |

## What was deleted

**None.** No Markdown files were removed. Previously deprecated *content* (sidebar Knowledge entry, `/admin/usage` UI route, Settings language switch) was already fixed; inventory now marks these as resolved regression targets.

## Architecture & diagram coverage

| Topic | Status |
|-------|--------|
| System topology | [overview.md](architecture/overview.md) — 8+ Mermaid diagrams |
| Technology choices & alternatives | [technical-decisions.md](architecture/technical-decisions.md) — comparison tables + Mermaid |
| KB ingestion pipeline | **New** [knowledge-base-ingestion.md](architecture/knowledge-base-ingestion.md) |
| Codebase ingestion | [codebase-reindex.md](architecture/codebase-reindex.md) |
| Gateway | **New** [nginx/README.md](../nginx/README.md) |
| Mind maps / draw.io | **Not present** — all diagrams are inline Mermaid (acceptable; no standalone `.drawio` files) |

## Bilingual coverage

- Formal docs: **paired** (`*.md` + `*.zh-CN.md`) for architecture, operations, tutorials, module READMEs, governance
- UI product copy: separate `ui/messages/en.json` / `zh.json` (locale code `zh`, doc filename `zh-CN` — documented in `ui/README.md`)
- Config comments: mixed (`.env.example` bilingual lines; `config.yaml` / nginx Chinese comments)

## Known remaining gaps (manual follow-up)

| Item | Priority | Notes |
|------|----------|-------|
| API route table drift | Medium | `api/README.md` route table is large; no auto-sync with `routes.py` — rely on PR checklist |
| OpenAPI in repo | Low | Runtime `/docs` only; offline API reference depends on `api/README.md` |
| CHANGELOG | Low | No project CHANGELOG; Git history / releases only |
| UI E2E test docs | Low | Frontend test coverage is thin; README should not overclaim |
| `ocr_llm_resolver.py` | N/A | Referenced in plan but not present in working tree; docs describe Worker inline resolution instead |

## Verification

```bash
./scripts/check-docs.sh   # passed 2026-07-06
```

## Related

- [Documentation inventory](DOCUMENTATION_INVENTORY.md)
- [Maintenance checklist](MAINTENANCE_CHECKLIST.md)
