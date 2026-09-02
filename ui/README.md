# Core UI v2

[简体中文](README.zh-CN.md)

The Next.js UI is a projection client for the v2 API. It submits commands and
renders durable state; it does not infer workflow completion locally.

Routes are deliberately small: `/`, `/runs/[id]`, `/approvals`, `/knowledge`,
`/settings`, `/teams`, `/admin`, `/login`, `/register`, and invitation acceptance.

```bash
npm install
npm run typecheck
npm run lint
npm test
npm run build
```

`src/lib/api-client.ts` is the HTTP boundary and `src/lib/navigation.ts` is the
single navigation catalog. The production API base is `/api`.
