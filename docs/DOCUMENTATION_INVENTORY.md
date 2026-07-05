[简体中文](DOCUMENTATION_INVENTORY.zh-CN.md)

# Documentation Inventory

Authoritative inventory of OpenCitadel Markdown documentation. Update this file when adding, moving, or deprecating docs.

**Legend**

| Column | Meaning |
|--------|---------|
| Authority | `primary` = source of truth; `index` = navigation only; `module` = component dev entry; `governance` = OSS policy |
| Bilingual | `paired` = `*.md` + `*.zh-CN.md`; `single` = one language only |
| Diagrams | `mermaid` / `none` |
| Stale risk | `low` / `medium` / `high` (manual review) |

## Root & docs hub

| Path | Topic | Authority | Bilingual | Diagrams | Code anchor | Stale risk |
|------|-------|-----------|-----------|----------|-------------|------------|
| [README.md](../README.md) | Project overview, quick start, doc map | index | paired | mermaid | — | medium |
| [docs/README.md](README.md) | Documentation navigation hub | index | paired | none | — | low |
| [docs/MAINTENANCE_CHECKLIST.md](MAINTENANCE_CHECKLIST.md) | PR checklist, sync rules | governance | paired | none | `scripts/check-docs.sh` | low |
| [docs/DOCUMENTATION_INVENTORY.md](DOCUMENTATION_INVENTORY.md) | This inventory | governance | paired | none | — | low |
| [docs/DOCUMENTATION_AUDIT_REPORT.md](DOCUMENTATION_AUDIT_REPORT.md) | Dated audit snapshot (historical) | governance | paired | none | — | low |

## Architecture (`docs/architecture/`)

| Path | Topic | Authority | Bilingual | Diagrams | Code anchor | Stale risk |
|------|-------|-----------|-----------|----------|-------------|------------|
| [overview.md](architecture/overview.md) | System design, API/Worker, DI, sandbox | primary | paired | mermaid | `api/app/container.py`, `worker/main.py` | medium |
| [security-model.md](architecture/security-model.md) | Trust boundaries, auth, secrets | primary | paired | mermaid | `api/app/infrastructure/security/` | medium |
| [events.md](architecture/events.md) | Domain events, SSE, replay | primary | paired | mermaid | `domain/models/event.py` | medium |
| [checkpoints-and-hitl.md](architecture/checkpoints-and-hitl.md) | HITL gates, checkpoints, Web Operator | primary | paired | mermaid | `checkpoint_service.py`, `session_routes.py` | medium |
| [web-operator.md](architecture/web-operator.md) | Gate profiles, audit contract | primary | paired | mermaid | `domain/services/agents/` | low |
| [teams-and-workspaces.md](architecture/teams-and-workspaces.md) | Teams, `X-Workspace-Id` | primary | paired | mermaid | `team_routes.py` | low |
| [admin-auditor-compliance.md](architecture/admin-auditor-compliance.md) | Admin, auditor, compliance | primary | paired | mermaid | `admin_routes.py`, `ui/src/app/admin/` | medium |
| [integrations-a2a-service-keys.md](architecture/integrations-a2a-service-keys.md) | A2A, service API keys | primary | paired | mermaid | `a2a_routes.py`, `service_api_key_routes.py` | low |
| [artifacts-sharing.md](architecture/artifacts-sharing.md) | Artifacts, public share | primary | paired | mermaid | `artifact_routes.py` | low |
| [automation-scheduler.md](architecture/automation-scheduler.md) | Cron, webhooks, leader election | primary | paired | mermaid | `scheduling_routes.py`, `worker/main.py` | low |
| [marketplace.md](architecture/marketplace.md) | Marketplace apps | primary | paired | mermaid | `marketplace_routes.py` | low |
| [config-source-governance.md](architecture/config-source-governance.md) | AppConfig vs env boundaries | primary | paired | mermaid | `core/config.py`, `app_config_routes.py` | medium |
| [model-resilience.md](architecture/model-resilience.md) | Circuit breaker, fallback | primary | paired | mermaid | `resilient_llm.py` | low |
| [contract-compatibility.md](architecture/contract-compatibility.md) | API/SSE compatibility window | primary | paired | mermaid | `event_upgrader.py` | low |
| [codebase-reindex.md](architecture/codebase-reindex.md) | Codebase ingest, vector recovery | primary | paired | mermaid | `codebase/ingestion_runner.py` | medium |
| [knowledge-base-ingestion.md](architecture/knowledge-base-ingestion.md) | KB parse, OCR, GraphRAG, ingest failures | primary | paired | mermaid | `knowledge_base/ingestion_runner.py` | medium |
| [architecture-evolution.md](architecture/architecture-evolution.md) | Compose → K8s evolution | primary | paired | mermaid | `deploy/helm/` | low |
| [llm-endpoints-and-models.md](architecture/llm-endpoints-and-models.md) | LLM endpoint/model split | primary | paired | mermaid | `llm_endpoint_routes.py`, `llm_model_routes.py` | low |
| [frontend-ui.md](architecture/frontend-ui.md) | Next.js UI architecture | primary | paired | mermaid | `ui/src/` | low |
| [task-recovery.md](architecture/task-recovery.md) | Recoverable task retry | primary | paired | mermaid | `recoverable_task_retry.py` | low |
| [technical-decisions.md](architecture/technical-decisions.md) | Technology choices & alternatives | primary | paired | mermaid | — | low |

## Operations & tutorials

| Path | Topic | Authority | Bilingual | Diagrams | Code anchor | Stale risk |
|------|-------|-----------|-----------|----------|-------------|------------|
| [operations/deployment.md](operations/deployment.md) | Production deployment | primary | paired | mermaid | `docker-compose.yml` | low |
| [operations/https-domain-setup.md](operations/https-domain-setup.md) | HTTPS & domain | primary | paired | none | `.env.example` | low |
| [tutorials/01-self-host-10-minutes.md](tutorials/01-self-host-10-minutes.md) | Quick BYO key onboarding | tutorial | paired | none | `scripts/quickstart.sh` | low |
| [tutorials/02-internal-knowledge-base.md](tutorials/02-internal-knowledge-base.md) | Knowledge base RAG | tutorial | paired | mermaid | `knowledge-base-ingestion.md` | low |
| [tutorials/03-mcp-integrations.md](tutorials/03-mcp-integrations.md) | MCP setup | tutorial | paired | none | `app_config_routes.py` | low |
| [tutorials/04-governed-web-operator.md](tutorials/04-governed-web-operator.md) | Web Operator runbook | tutorial | paired | none | `operator-scope-dialog.tsx` | low |
| [tutorials/05-refund-reconciliation-compliance.md](tutorials/05-refund-reconciliation-compliance.md) | Compliance demo | tutorial | paired | none | `compliance_routes.py` | low |

## Module READMEs

| Path | Topic | Authority | Bilingual | Diagrams | Code anchor | Stale risk |
|------|-------|-----------|-----------|----------|-------------|------------|
| [api/README.md](../api/README.md) | Backend routes, SSE, dev | module | paired | none | `interfaces/endpoints/` | medium |
| [ui/README.md](../ui/README.md) | Frontend stack, routes | module | paired | none | `ui/src/app/` | medium |
| [sandbox/README.md](../sandbox/README.md) | Sandbox service | module | paired | none | `sandbox/` | low |
| [nginx/README.md](../nginx/README.md) | Gateway, SSE/WS, upload limits | module | paired | mermaid | `nginx/nginx.conf` | low |
| [deploy/helm/opencitadel/README.md](../deploy/helm/opencitadel/README.md) | Helm install | module | paired | none | `deploy/helm/` | low |
| [demo/ops-console/README.md](../demo/ops-console/README.md) | Web Operator demo backend | module | paired | none | `demo/ops-console/` | low |
| [e2e/README.md](../e2e/README.md) | Playwright E2E smoke tests | module | paired | none | `e2e/web-operator.spec.ts` | low |
| [scripts/README.md](../scripts/README.md) | quickstart, check-docs | module | paired | none | `scripts/` | low |
| [deploy/scripts/README.md](../deploy/scripts/README.md) | Host tuning scripts | module | paired | none | `deploy/scripts/` | low |

## Open-source governance (`.github/`)

| Path | Topic | Authority | Bilingual | Diagrams | Stale risk |
|------|-------|-----------|-----------|----------|------------|
| [CONTRIBUTING.md](../.github/CONTRIBUTING.md) | Contribution guide | governance | paired | none | low |
| [SECURITY.md](../.github/SECURITY.md) | Vulnerability reporting | governance | paired | none | low |
| [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.md) | Community standards | governance | paired | none | low |
| [pull_request_template.md](../.github/pull_request_template.md) | PR template | governance | paired | none | low |

## Deprecation candidates (resolved — kept for grep regression)

| Location | Issue | Action |
|----------|-------|--------|
| `ui/README.md` | “Language switch planned in Settings” | Fixed — Header `LanguageToggle` |
| `admin-auditor-compliance.md` | `/admin/usage` UI route | Fixed — usage charts on `/admin` overview |
| Tutorials | “Knowledge in sidebar” | Fixed — Header workspace menu |
| Multiple docs | Duplicate LLM model-only setup steps | Dedupe — link to `deployment.md` |
| `DOCUMENTATION_INVENTORY` | `technical-decisions` marked `none` for diagrams | Fixed — includes Mermaid |

## Maintenance

- Run `./scripts/check-docs.sh` before doc PRs.
- When code changes routes, config, or UI flows, update the matching row’s doc and set stale risk back to `low` after review.
- New architecture topics: add EN + zh-CN, link from [docs/README.md](README.md), update this inventory.
