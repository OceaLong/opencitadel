[简体中文](MAINTENANCE_CHECKLIST.zh-CN.md)

# Documentation Maintenance Checklist

Use this checklist when changing features, routes, configuration, deployment, or UI copy.

**Related governance docs**

| Document | Role |
|----------|------|
| [Documentation inventory](DOCUMENTATION_INVENTORY.md) | Authoritative list of all docs, authority level, stale risk |
| This checklist | Actionable PR steps for contributors |

## When to update docs

- [ ] New or changed API route → `api/README.md` + `api/README.zh-CN.md`, relevant `docs/architecture/*.md`
- [ ] New or changed inference endpoint/model/binding behavior → `inference-control-plane.md` (+ zh), `deployment.md` (+ zh), `ui/README.md` (+ zh)
- [ ] New or changed UI route or approval component → `ui/README.md` + `ui/README.zh-CN.md`, `frontend-ui.md` (+ zh), root README module table
- [ ] New env var → `.env.example`, `docs/operations/deployment.md` (+ zh), `config-source-governance.md` (+ zh)
- [ ] New Runtime Policy field → typed policy model, seed, admin form, OpenAPI contract, `runtime-policy-control-plane.md` (+ zh)
- [ ] New tutorial or architecture doc → both language files, `docs/README.md` (+ zh), root `README.md` (+ zh), top language links
- [ ] KB/Codebase ingestion change → `knowledge-base-ingestion.md` (+ zh), `codebase-reindex.md` (+ zh), tutorial 02 (+ zh), `execution-kernel.md` (+ zh)
- [ ] Upload limit change → `nginx/README.md` (+ zh), `ui/src/lib/constants.ts`, `config-source-governance.md` (+ zh), `deployment.md` (+ zh)
- [ ] Docker image name/count change → `deployment.md` (+ zh), Helm README (+ zh), `release.yml` comment if needed
- [ ] Patrol Pack/Run/Collector change → `ops-patrol.md` architecture + operations + tutorial pairs, Collector README, API/UI READMEs, deployment examples

## Bilingual sync

- [ ] English `topic.md` and Chinese `topic.zh-CN.md` updated together
- [ ] Top-of-file links: `[English](topic.md) · [简体中文](topic.zh-CN.md)` (or reverse on zh file)
- [ ] Internal links: English docs → `*.md`; Chinese docs → `*.zh-CN.md`
- [ ] UI copy updated directly in both authoritative catalogs: `ui/messages/en.json` and `ui/messages/zh.json`
- [ ] `cd ui && npm run i18n:check` passes with no locale mismatch, missing/unused key, unknown dynamic call, orphan expansion, or hardcoded UI finding

## Accuracy checks (manual)

| Area | Verify against |
|------|----------------|
| UI routes | `ui/src/app/**/page.tsx` |
| API routes | `api/app/interfaces/endpoints/routes.py` and route modules |
| Inference control plane | `inference_routes.py`, `inference-settings.tsx`, Settings endpoint/model/binding flow |
| Run recovery | `application/execution/`, `execution-kernel.md` (+ zh) |
| Compose images | `docker-compose.yml`, `.github/workflows/release.yml` |
| Sandbox boundary | Chromium in sandbox; execution kernel connects through CDP |
| Integrations UI | Settings modal → Integrations tab (not `/settings/integrations`) |
| Object storage | `.env.example` defaults; quickstart sets `COMPOSE_PROFILES=local` + `STORAGE_PROVIDER=minio` for first run |
| Upload limits | `nginx/nginx.conf`, `ui/src/lib/constants.ts`, Execution Policy `knowledge_base.document.max_bytes` |
| KB ingest / OCR | `knowledge_base/ingestion_runner.py`, `application/execution/activities/resource_build.py`, `knowledge-base-ingestion.md` (+ zh) |
| Service API Key | `X-Api-Key` header; inbound `/api/a2a` only |
| Share links | Default TTL 168h; `/share/artifact/[token]` UI route |
| Ops Patrol | `patrol_routes.py`, Pack/Run services, built-in template, `ops-collector/config.py`, Helm/Kustomize manifests |

## Automated check

Run before opening a PR:

```bash
make quality-check
./scripts/check-docs.sh
```

CI runs the same quality and documentation checks on every pull request.

## Related

- [Docs index](README.md)
- [Documentation inventory](DOCUMENTATION_INVENTORY.md)
- [Contributing](../.github/CONTRIBUTING.md)
