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
6. [Read-only daily Ops Patrol](tutorials/06-ops-patrol.md)
7. [Approve an Ops Patrol remediation](tutorials/07-approved-remediation.md)
8. [The 10-minute governance demo loop](tutorials/08-ten-minute-governance-demo.md)

### Operations & deployment

| Document | Scope |
|----------|-------|
| [README.md](../README.md) | Project overview and doc map |
| [Production deployment](operations/deployment.md) | Docker Compose production deployment, cloud/local modes, backup, tuning |
| [Ops Patrol operations](operations/ops-patrol.md) | Collector security boundary, deployment, recovery, evidence, troubleshooting |
| [HTTPS & domain setup](operations/https-domain-setup.md) | Domain binding and HTTPS |
| [Helm Chart](../deploy/helm/opencitadel/README.md) | Kubernetes / Helm install and values |

### Architecture & design

| Document | Scope |
|----------|-------|
| [Architecture overview](architecture/overview.md) | System design, process roles, sandbox lifecycle, deployment modes |
| [Governance plane](architecture/governance-plane.md) | Effect contracts, capability narrowing, batch approval, terminal latch, evidence |
| [Ops Patrol](architecture/ops-patrol.md) | Pack/Run lifecycle, Collector trust boundary, evidence, tenant isolation |
| [Technical decisions](architecture/technical-decisions.md) | Technology choices and alternatives |
| [Inference control plane](architecture/inference-control-plane.md) | Endpoint/model/binding ownership, capabilities, encryption, UI flow |
| [Frontend UI](architecture/frontend-ui.md) | Next.js shell, public SSE projection, approval surfaces |
| [Execution kernel](architecture/execution-kernel.md) | Commands, Event Store, Activities, recovery, projections, SSE, and privilege boundaries |
| [Execution kernel cutover evidence](architecture/execution-kernel-cutover-evidence.md) | Greenfield schema boundary and reproducible acceptance results |
| [Security model](architecture/security-model.md) | Trust boundaries, sandbox isolation, auth |
| [Web Operator](architecture/web-operator.md) | Exact-host boundary, per-invocation approval, evidence |
| [Teams & workspaces](architecture/teams-and-workspaces.md) | Team roles, `X-Workspace-Id`, invitations |
| [Admin, auditor & compliance](architecture/admin-auditor-compliance.md) | Platform admin, evidence chain, compliance reports |
| [A2A & service API keys](architecture/integrations-a2a-service-keys.md) | Inbound/outbound A2A, `X-Api-Key` |
| [Skills](architecture/skills.md) | Skill templates, runtime overrides, MCP/A2A filtering |
| [Artifacts & sharing](architecture/artifacts-sharing.md) | Session artifacts, public share links |
| [Automation & scheduler](architecture/automation-scheduler.md) | Cron/webhook jobs, leader election, notifications |
| [Config source governance](architecture/config-source-governance.md) | Deployment Settings, Runtime Policy, Integration boundaries |
| [Runtime Policy control plane](architecture/runtime-policy-control-plane.md) | Immutable revisions, atomic head, CAS, fail-closed consumers |
| [Model resilience](architecture/model-resilience.md) | Circuit breaking, fallback, SLO runbooks |
| [Codebase reindex](architecture/codebase-reindex.md) | Vector degradation and recovery |
| [Knowledge base ingestion](architecture/knowledge-base-ingestion.md) | Parse, OCR, GraphRAG, ingest failures |
| [Architecture evolution](architecture/architecture-evolution.md) | Compose → K8s / external sandbox |

### Module guides

| Document | Scope |
|----------|-------|
| [API](../api/README.md) | Backend routes, SSE, local dev |
| [UI](../ui/README.md) | Frontend stack and routes |
| [Sandbox](../sandbox/README.md) | Isolated runtime |
| [Nginx gateway](../nginx/README.md) | Edge proxy, SSE/WS, upload limits |
| [Ops Collector](../ops-collector/README.md) | Fixed read-only MCP probes, configuration and deployment |
| [Ops Actuator](../ops-actuator/README.md) | Fixed patch-only write MCP probes, configuration and deployment |
| [OpsConsole demo](../demo/ops-console/README.md) | Web Operator ticket backend demo |
| [E2E acceptance](../e2e/README.md) | Deterministic isolated full-stack gate, evidence, and cleanup |
| [Repository scripts](../scripts/README.md) | `quickstart.sh`, `check-docs.sh`, acceptance runner |
| [Deploy scripts](../deploy/scripts/README.md) | Production host tuning utilities |

### Open-source governance

| Document | Scope |
|----------|-------|
| [CONTRIBUTING.md](../.github/CONTRIBUTING.md) | Contribution guide |
| [SECURITY.md](../.github/SECURITY.md) | Vulnerability reporting |
| [CODE_OF_CONDUCT.md](../.github/CODE_OF_CONDUCT.md) | Community standards |

## Maintenance rules

- **One topic, one authoritative doc** — avoid duplicating policy across README and topic docs.
- **Config source of truth** — `.env.example` for deployment inputs; PostgreSQL Runtime Policy revisions for live behavior.
- **Bilingual pairs** — update both language files when changing a topic.
- **Link convention** — English docs link to `*.md`; Chinese docs link to `*.zh-CN.md`.
- **Index sync** — when adding a tutorial or architecture doc, update this index, the root [README.md](../README.md) / [README.zh-CN.md](../README.zh-CN.md) doc map, and add top-of-file language switch links in both language files.
- **PR checklist** — [Documentation maintenance checklist](MAINTENANCE_CHECKLIST.md) (actionable steps); [Documentation inventory](DOCUMENTATION_INVENTORY.md) (live authoritative list); run `./scripts/check-docs.sh` before submitting doc changes.
