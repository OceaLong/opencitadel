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
| `/admin/*` | Admin console (7 pages) | Admin layout |
| `/invitations/[token]` | Accept invitation | No shell |
| `/share/artifact/[token]` | Public artifact view | No shell |

**Navigation**: Left panel holds session list; Codebase, Knowledge, Marketplace, and Automation are in the **header workspace dropdown** (`app-header.tsx`).

## Features

- **Session config**: choose model and Skill on home and session pages; update when session is idle.
- **Streaming chat**: SSE with `message_delta` merge; history via `/sessions/{id}/events`.
- **Endpoint & model management**: Settings → Models — group models by endpoint (provider, base URL, API key on endpoint).
- **Skill templates**: Settings → Skills.
- **Long-term memory**: Settings → Memory; compact/clear session memory on session page.
- **Settings modal** (six tabs): Common, Models, Skills, Memory, Integrations (MCP/A2A), Runtime (admin only).
- **Language toggle**: Header `LanguageToggle` (`NEXT_LOCALE` cookie; no URL prefix).
- **HITL UI**: clarify questions, plan/tool approval bars, VNC takeover, checkpoint restore.
- **Web Operator**: `operator-scope-dialog.tsx` when Skill is `web-operator`.
- **LLM status badge**: Header polls `/api/llm/status`.

See [`../docs/architecture/frontend-ui.md`](../docs/architecture/frontend-ui.md).

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
| `knowledge.ts`, `codebase.ts` | Knowledge bases |
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

## Internationalization (i18n)

- Framework: `next-intl` with locales `en` and `zh` (default `en`)
- Message source: `scripts/build-messages.mjs` → `npm run i18n:build` → `messages/en.json` and `messages/zh.json`
- Runtime locale code is `zh`; documentation filenames use `*.zh-CN.md` for Chinese — same language, different identifiers
- URL has no locale prefix (`localePrefix: "never"`); locale persisted in `NEXT_LOCALE` cookie
- Language switch: **AppHeader** → `LanguageToggle` component
