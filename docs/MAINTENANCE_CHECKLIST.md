[简体中文](MAINTENANCE_CHECKLIST.zh-CN.md)

# Documentation Maintenance Checklist

Use this checklist when changing features, routes, configuration, deployment, or UI copy.

**Related governance docs**

| Document | Role |
|----------|------|
| [Documentation inventory](DOCUMENTATION_INVENTORY.md) | Authoritative list of all docs, authority level, stale risk |
| [Documentation audit report](DOCUMENTATION_AUDIT_REPORT.md) | Dated snapshot of a past audit — do not treat as live status |
| This checklist | Actionable PR steps for contributors |

## When to update docs

- [ ] New or changed API route → `api/README.md` + `api/README.zh-CN.md`, relevant `docs/architecture/*.md`
- [ ] New or changed LLM endpoint/model behavior → `llm-endpoints-and-models.md` (+ zh), `deployment.md` (+ zh), `ui/README.md` (+ zh)
- [ ] New or changed UI route or HITL component → `ui/README.md` + `ui/README.zh-CN.md`, `frontend-ui.md` (+ zh), root README module table
- [ ] New env var → `.env.example`, `docs/operations/deployment.md` (+ zh), `config-source-governance.md` (+ zh)
- [ ] New `AppConfig` field → `api/config.yaml`, Helm `appConfig`, `config-source-governance.md` (+ zh)
- [ ] New tutorial or architecture doc → both language files, `docs/README.md` (+ zh), root `README.md` (+ zh), top language links
- [ ] KB/Codebase ingestion change → `knowledge-base-ingestion.md` (+ zh), `codebase-reindex.md` (+ zh), tutorial 02 (+ zh), `events.md` (+ zh)
- [ ] Upload limit change → `nginx/README.md` (+ zh), `ui/src/lib/constants.ts`, `config-source-governance.md` (+ zh), `deployment.md` (+ zh)
- [ ] Docker image name/count change → `deployment.md` (+ zh), Helm README (+ zh), `release.yml` comment if needed

## Bilingual sync

- [ ] English `topic.md` and Chinese `topic.zh-CN.md` updated together
- [ ] Top-of-file links: `[English](topic.md) · [简体中文](topic.zh-CN.md)` (or reverse on zh file)
- [ ] Internal links: English docs → `*.md`; Chinese docs → `*.zh-CN.md`
- [ ] UI i18n keys added in `ui/scripts/build-messages.mjs` for both `en` and `zh`

## Accuracy checks (manual)

| Area | Verify against |
|------|----------------|
| UI routes | `ui/src/app/**/page.tsx` |
| API routes | `api/app/interfaces/endpoints/routes.py` and route modules |
| LLM endpoints/models | `llm_endpoint_routes.py`, `models-settings.tsx`, Settings two-step flow |
| Task recovery | `recoverable_task_retry.py`, `task-recovery.md` (+ zh) |
| Compose images | `docker-compose.yml`, `.github/workflows/release.yml` |
| Sandbox boundary | Chromium in sandbox; Playwright in Worker via CDP |
| Integrations UI | Settings modal → Integrations tab (not `/settings/integrations`) |
| Object storage | `.env.example` defaults; quickstart sets `COMPOSE_PROFILES=local` + `STORAGE_PROVIDER=minio` for first run |
| Upload limits | `nginx/nginx.conf`, `ui/src/lib/constants.ts`, AppConfig `knowledge_base.document.max_bytes`, `server.marketplace_max_upload_bytes` |
| KB ingest / OCR | `knowledge_base/ingestion_runner.py`, `worker/main.py`, `knowledge-base-ingestion.md` (+ zh) |
| Service API Key | `X-Api-Key` header; inbound `/api/a2a` only |
| Share links | Default TTL 168h; `/share/artifact/[token]` UI route |

## Automated check

Run before opening a docs PR:

```bash
./scripts/check-docs.sh
```

CI runs the same script on every pull request.

## Related

- [Docs index](README.md)
- [Documentation inventory](DOCUMENTATION_INVENTORY.md)
- [Contributing](../.github/CONTRIBUTING.md)
