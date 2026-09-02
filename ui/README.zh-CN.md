# v2 核心 UI

[English](README.md)

Next.js UI 是 v2 API 的 Projection 客户端：只提交命令并渲染持久状态，不在本地
推断工作流是否完成。

路由刻意保持精简：`/`、`/runs/[id]`、`/approvals`、`/knowledge`、
`/settings`、`/teams`、`/admin`、`/login`、`/register` 和邀请接受页。

```bash
npm install
npm run typecheck
npm run lint
npm test
npm run build
```

`src/lib/api-client.ts` 是唯一 HTTP 边界，`src/lib/navigation.ts` 是唯一导航目录。
生产 API 根路径为 `/api`。
