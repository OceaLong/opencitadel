[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel UI

Next.js frontend for session management, AI chat, model/endpoint settings, Skill templates, long-term memory, HITL gates, and remote desktop (VNC).

## Tech Stack

- Next.js 16 (React 19, App Router)
- TypeScript
- Tailwind CSS 4
- Radix UI + Lucide icons
- next-intl 4.x (locales `en` / `zh`)
- noVNC (remote desktop), Mermaid (diagrams), Recharts (admin charts)

## Project Structure

```
ui/
├── src/
│   ├── app/               # App Router pages
│   │   ├── page.tsx       # Home (new session)
│   │   ├── sessions/      # Session detail
│   │   ├── codebase/      # Code knowledge base
│   │   ├── knowledge/     # Document knowledge base
│   │   ├── marketplace/   # Marketplace mini-apps
│   │   ├── automation/    # Scheduled jobs
│   │   ├── teams/         # Team workspaces
│   │   ├── admin/         # Admin console
│   │   ├── login/         # Login
│   │   ├── register/      # Registration
│   │   ├── invitations/   # Team invitation accept
│   │   └── share/         # Public artifact share
│   ├── components/        # UI components
│   │   ├── settings/      # Settings tabs (models, skills, memory...)
│   │   ├── ui/            # Base Radix UI primitives
│   │   └── tool-use/      # Tool execution UI
│   ├── lib/
│   │   └── api/           # API client modules
│   ├── hooks/             # Custom hooks
│   ├── providers/         # React context providers
│   └── i18n/              # next-intl routing and locale helpers
├── scripts/               # i18n build/check scripts
├── messages/              # Generated en.json / zh.json
└── public/
```

## Routes

| Route | Page | Shell |
|-------|------|-------|
| `/` | Home — create session, model/Skill pick | Sidebar + header |
| `/sessions/[id]` | Streaming chat, HITL, VNC, artifacts | Sidebar + header |
| `/codebase`, `/codebase/[id]` | Code import and Ask/Agent | Sidebar + header |
| `/knowledge`, `/knowledge/[id]` | Document KB and RAG | Sidebar + header |
| `/marketplace` | LLM mini-apps | Sidebar + header |
| `/automation` | Cron/webhook jobs | Sidebar + header |
| `/teams`, `/teams/[id]` | Team management | Sidebar + header |
| `/login`, `/register` | Auth | No shell |
| `/admin` | Overview dashboard (usage charts) | Admin layout |
| `/admin/users` | User management | Admin layout |
| `/admin/teams` | Team administration | Admin layout |
| `/admin/invitations` | Platform invitations | Admin layout |
| `/admin/audit` | Audit log viewer | Admin layout |
| `/admin/compliance` | Evidence center | Admin layout |
| `/admin/compliance/report` | Compliance report export | Admin layout |
| `/invitations/[token]` | Accept invitation | No shell |
| `/share/artifact/[token]` | Public artifact view | No shell |

**Navigation**:

- **Desktop**: Left panel holds session list; Codebase, Knowledge, Marketplace, and Automation are in the **header workspace dropdown** (`app-header.tsx`).
- **Mobile**: `MobileBottomNav` exposes chat, codebase, knowledge, and marketplace; Automation, Teams, Settings, and Admin are in the **More** sheet.
- `/codebase/[id]` and `/knowledge/[id]` redirect to a new Ask session — they are not standalone detail pages.

## Features

- **Session config**: choose model and Skill on home and session pages; update when session is idle.
- **Streaming chat**: SSE with `message_delta` merge; history via `/sessions/{id}/events`.
- **Endpoint & model management**: Settings → Models — group models by endpoint (provider, base URL, API key on endpoint).
- **Skill templates**: Settings → Skills.
- **Long-term memory**: Settings → Memory; compact/clear session memory on session page.
- **Settings modal** (eight tabs): General (theme + language), Agent, Models, Skills, Memory, Integrations (MCP/A2A/Service Keys), HITL, Runtime (admin only).
- **Theme and language**: Settings → General (`GeneralSettings`); locale in `NEXT_LOCALE` cookie (no URL prefix).
- **HITL UI**: clarify questions, plan/tool approval bars, VNC takeover, checkpoint restore; global defaults in Settings → HITL.
- **Web Operator**: `operator-scope-dialog.tsx` when Skill is `web-operator`.
- **LLM status badge**: Header polls `/api/llm/status`.
- **Notifications**: `NotificationInbox` in header (REST + SSE).
- **Mobile session toolbar**: model/Skill/context options in `ChatOptionsSheet`.

See [`../docs/architecture/frontend-ui.md`](../docs/architecture/frontend-ui.md).

## Frontend development conventions

Contributors should follow these conventions (also enforced in `ui/.cursor/rules.general.mdc` for Cursor IDE):

- **TypeScript strict mode**; prefer `type` over `interface`; path alias `@/*` → `./src/*`
- **App Router**: server components by default; add `"use client"` only when needed; pages in `src/app/**/page.tsx`
- **Components**: base UI in `@/components/ui/` (shadcn/Radix); business components in `@/components/`; use `cn()` and CVA for variants
- **Hooks**: extract complex state/effects from large pages into `src/hooks/`
- **Imports**: React/Next → third-party → `@/components` → `@/lib`/`@/hooks` → relative
- **Formatting**: Prettier (100 cols, double quotes); run `npm run format:check` before PR
- **API client**: use `src/lib/api/fetch.ts`; never hardcode routes outside `src/lib/api/`

## API Client

Base URL via `NEXT_PUBLIC_API_BASE_URL`:

- **Development**: `http://localhost:8088/api`
- **Production**: `/api` (Nginx reverse proxy)

Core fetch layer: `src/lib/api/fetch.ts` — cookie auth, CSRF, `X-Workspace-Id`, 401 refresh, SSE helpers.

| Module | Purpose |
|--------|---------|
| `session.ts` | Sessions, chat SSE, checkpoints |
| `endpoints.ts` | `/llm-endpoints` CRUD |
| `models.ts` | `/llm-models` CRUD |
| `llm-status.ts` | `/llm/status` |
| `config.ts` | AppConfig sections |
| `skills.ts`, `memory.ts` | Skills and memory |
| `admin.ts`, `team.ts` | Admin and teams |
| `knowledge.ts`, `codebase.ts`, `file.ts` | Knowledge bases, codebases, files |
| `service-keys.ts`, `notifications.ts`, `compliance.ts` | Service API keys, notifications, compliance |
| `constants.ts` | Shared limits (`CODEBASE_ZIP_MAX_BYTES` = 200 MB, must match nginx) |
| `artifacts.ts` | Artifacts and share |
| `types.ts` | Shared TypeScript types |

## Local Development

### Prerequisites

- Node.js >= 22
- npm >= 10

### Install and Run

```bash
npm install
npm run dev
```

Dev server: `http://localhost:3000`; API default: `http://localhost:8088/api`.

### Code Quality

```bash
npm run lint
npm run lint:fix
npm run typecheck
npm run format
npm run format:check
npm run i18n:check
```

### Build

```bash
npm run build
npm run start
```

## Docker Deployment

UI deploys via root `docker-compose.yml`. Multi-stage Dockerfile:

1. **deps** — install npm dependencies
2. **builder** — build Next.js (standalone)
3. **runner** — minimal production image

Build with `NEXT_PUBLIC_API_BASE_URL=/api` so Nginx proxies API requests.

## Testing

- **Unit tests** (Vitest): logic-layer tests under `ui/src/**/*.test.ts` — safe redirect, session events, LLM status, knowledge utils. No component-level UI regression suite.
- **E2E** (Playwright): smoke tests in `e2e/` — home page load and OpsConsole demo login only. See [`../e2e/README.md`](../e2e/README.md).

Do not assume `npm run test` covers full UI flows.

## Internationalization (i18n)

- Framework: `next-intl` with locales `en` and `zh` (default `en`)
- **Source of truth**: `scripts/build-messages.mjs` (+ `i18n-supplement.mjs` for drift backfill) → `npm run i18n:build` → `messages/en.json` and `messages/zh.json`
- **Do not hand-edit** `messages/*.json` without updating the build scripts and re-running `i18n:build`
- Runtime locale code is `zh`; documentation filenames use `*.zh-CN.md` for Chinese — same language, different identifiers
- URL has no locale prefix (`localePrefix: "never"`); locale persisted in `NEXT_LOCALE` cookie
- Theme and language: **Settings → General** (`GeneralSettings`)
