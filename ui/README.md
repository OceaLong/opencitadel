# OpenCitadel UI

[简体中文](README.zh-CN.md)

Next.js 16 / React 19 frontend for event-sourced Agent sessions, immutable
knowledge and codebase versions, automation, patrol, governance, and platform
administration.

## Contract boundary

The UI is a projection client. It submits API commands and renders formal
Run, Activity, approval, resource-build, and public-event views. It never
infers workflow completion from connection state or local timers.

- Session live delivery and replay use the same public execution-event model.
- Durable cursors are opaque to components.
- Approval actions target persisted approval batches; chat text is not an
  approval protocol.
- Internal Activity payloads, provider secrets, and event hashes are not part
  of the browser contract.
- Resource sessions pin an immutable published version.

## Source map

```text
src/
├── app/                 App Router pages
├── components/
│   ├── session/         timeline, approvals, errors, VNC, artifacts
│   ├── resource/        candidate build and version status
│   ├── knowledge/       knowledge library and document reader
│   ├── codebase/        codebase library
│   ├── patrol/          patrol and remediation views
│   ├── admin/           governance, usage, compliance
│   ├── settings/        general, Agent, inference, Skills, memory, integrations, runtime
│   └── ui/              shared Radix primitives
├── hooks/               state and streaming orchestration
├── lib/api/             typed HTTP/SSE clients
├── lib/session-events.ts
├── providers/
└── i18n/
messages/                authoritative English and Chinese catalogs
scripts/                 strict i18n consistency checks
```

Important routes include `/sessions/[id]`, `/knowledge`, `/codebase`,
`/automation`, `/patrols`, `/patrol-runs/[id]`, `/teams`, and `/admin/*`.
Settings contains General, Agent, Inference, Skills, Memory, Integrations, and an
administrator-only Runtime section.

## Development

```bash
npm install
npm run i18n:check
npm run typecheck
npm run lint
npm run test
npm run build
```

`messages/en.json` and `messages/zh.json` are the only translation sources.
Update both catalogs directly; `npm run i18n:check` rejects mismatched,
missing, unused, unregistered dynamic, or hardcoded user-facing text.

Use `src/lib/api/fetch.ts` for API access, preserve strict TypeScript, keep
domain components in their domain directory, and avoid hard-coded API routes
outside `src/lib/api/`.

The development server runs on `http://localhost:3000`; the default API base
is `http://localhost:8088/api`. Production uses `/api` through the reverse
proxy.

See [frontend architecture](../docs/architecture/frontend-ui.md) and
[execution kernel](../docs/architecture/execution-kernel.md).
