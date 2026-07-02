[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel UI

Next.js frontend for session management, AI chat, model and Skill selection, long-term memory, and remote desktop (VNC).

## Tech Stack

- Next.js 16 (React 19)
- TypeScript
- Tailwind CSS 4
- Radix UI
- noVNC (remote desktop)

## Project Structure

```
ui/
├── src/
│   ├── app/               # Page routes
│   │   ├── page.tsx       # Home
│   │   ├── sessions/      # Session pages
│   │   ├── marketplace/   # Marketplace mini-apps
│   │   ├── automation/    # Scheduled jobs
│   │   └── settings/      # Settings (models, Skills, memory, integrations)
│   ├── components/        # Components
│   │   ├── ui/            # Base UI components
│   │   └── tool-use/      # Tool-use components
│   ├── lib/
│   │   └── api/           # API client
│   ├── hooks/             # Custom hooks
│   └── providers/         # Context providers
├── public/                # Static assets
├── eslint.config.mjs      # ESLint + import sorting
├── .prettierrc            # Prettier config
├── next.config.ts         # Next.js config
├── Dockerfile
├── package.json
└── tsconfig.json
```

## Features

- **Session config**: choose model and Skill on home and session pages; update config when session is idle.
- **Streaming chat**: SSE events with Token-level `message_delta` merge; history via `events_next_cursor` and `/sessions/{id}/events`.
- **Model management**: Settings → Models for multi-provider CRUD and default model.
- **Skill templates**: Settings → Skills for system prompts, tools, recommended models, Agent params, and example questions.
- **Long-term memory**: Settings → Memory for global or session memory; compact, clear, or delete Agent memory on session page.
- **Settings modal**: general config; MCP and A2A management also available at **Settings → Integrations** (`/settings/integrations`).
- **Marketplace**: `/marketplace` LLM mini-apps catalog.
- **Automation**: `/automation` scheduled jobs and notifications.

## API Client

Configure API base URL via `NEXT_PUBLIC_API_BASE_URL`:

- **Development**: default `http://localhost:8088/api` (direct to API)
- **Production**: build with `/api` (Nginx reverse proxy)

API modules live in `src/lib/api/` (sessions, files, app config, models, Skills, memory).

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
