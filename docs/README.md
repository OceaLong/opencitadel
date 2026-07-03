# OpenCitadel Documentation

[简体中文](README.zh-CN.md)

Navigation hub for all OpenCitadel documentation. Each topic is maintained as a **paired document**: `*.md` (English) and `*.zh-CN.md` (Chinese).

## Recommended paths

### Getting started

1. [Self-host in 10 minutes](tutorials/01-self-host-10-minutes.md)
2. [Internal knowledge base](tutorials/02-internal-knowledge-base.md)
3. [MCP integrations](tutorials/03-mcp-integrations.md)
4. [Governed Web Operator](tutorials/04-governed-web-operator.md)
5. [Refund reconciliation & compliance](tutorials/05-refund-reconciliation-compliance.md)

### Operations & deployment

| Document | Scope |
|----------|-------|
| [README.md](../README.md) | Project overview and doc map |
| [Production deployment](operations/deployment.md) | Docker Compose production deployment, cloud/local modes, backup, tuning |
| [HTTPS & domain setup](operations/https-domain-setup.md) | Domain binding and HTTPS |
| [Helm Chart](../deploy/helm/opencitadel/README.md) | Kubernetes / Helm install and values |

### Architecture & design

| Document | Scope |
|----------|-------|
| [Architecture overview](architecture/overview.md) | System design, process roles, sandbox lifecycle, deployment modes |
| [Security model](architecture/security-model.md) | Trust boundaries, sandbox isolation, auth |
| [Events](architecture/events.md) | Domain events, SSE contract, persistence, replay |
| [Checkpoints & HITL](architecture/checkpoints-and-hitl.md) | Gate contracts, rollback, Web Operator, browser profile snapshots |
| [Web Operator](architecture/web-operator.md) | Gate profiles, audit contract, OpsConsole demo |
| [Teams & workspaces](architecture/teams-and-workspaces.md) | Team roles, `X-Workspace-Id`, invitations |
| [Admin, auditor & compliance](architecture/admin-auditor-compliance.md) | Platform admin, evidence chain, compliance reports |
| [A2A & service API keys](architecture/integrations-a2a-service-keys.md) | Inbound/outbound A2A, `X-Api-Key` |
| [Artifacts & sharing](architecture/artifacts-sharing.md) | Session artifacts, public share links |
| [Automation & scheduler](architecture/automation-scheduler.md) | Cron/webhook jobs, leader election, notifications |
| [Marketplace](architecture/marketplace.md) | LLM mini-app catalog and contracts |
| [Config source governance](architecture/config-source-governance.md) | AppConfig, config.yaml, env var boundaries |
| [Model resilience](architecture/model-resilience.md) | Circuit breaking, fallback, SLO runbooks |
| [Contract compatibility](architecture/contract-compatibility.md) | API/SSE compatibility window |
| [Codebase reindex](architecture/codebase-reindex.md) | Vector degradation and recovery |
| [Architecture evolution](architecture/architecture-evolution.md) | Compose → K8s / external sandbox |

### Module guides

| Document | Scope |
|----------|-------|
| [API](../api/README.md) | Backend routes, SSE, local dev |
| [UI](../ui/README.md) | Frontend stack and routes |
| [Sandbox](../sandbox/README.md) | Isolated runtime |

### Open-source governance

| Document | Scope |
|----------|-------|
| [CONTRIBUTING.md](../.github/CONTRIBUTING.md) | Contribution guide |
| [SECURITY.md](../.github/SECURITY.md) | Vulnerability reporting |
| [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.md) | Community standards |

## Maintenance rules

- **One topic, one authoritative doc** — avoid duplicating policy across README and topic docs.
- **Config source of truth** — `.env.example` for env vars; `api/config.yaml` for behavior config.
- **Bilingual pairs** — update both language files when changing a topic.
- **Link convention** — English docs link to `*.md`; Chinese docs link to `*.zh-CN.md`.
- **Index sync** — when adding a tutorial or architecture doc, update this index, the root [README.md](../README.md) / [README.zh-CN.md](../README.zh-CN.md) doc map, and add top-of-file language switch links in both language files.
- **PR checklist** — [Documentation maintenance checklist](MAINTENANCE_CHECKLIST.md); run `./scripts/check-docs.sh` before submitting doc changes.
