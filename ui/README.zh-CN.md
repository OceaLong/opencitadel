# OpenCitadel UI

[English](README.md)

基于 Next.js 16 / React 19 的前端，覆盖事件溯源 Agent 会话、不可变知识版本、
自动化、巡检、治理与平台管理。

## 契约边界

UI 是投影客户端：提交 API Command，展示正式 Run、Activity、审批、资源构建和公开事件
投影；不会根据连接状态或本地 Timer 推断工作流完成。

- 会话实时流与回放使用同一公开执行事件模型。
- 组件把持久 Cursor 当作不透明值。
- 审批动作针对持久 Approval Batch；聊天文本不是审批协议。
- Activity 私有 Payload、Provider Secret 与事件哈希不进入浏览器契约。
- 资源会话固定绑定一个不可变已发布版本。

## 源码地图

```text
src/
├── app/                 App Router 页面
├── components/
│   ├── session/         时间线、审批、错误、VNC、交付物
│   ├── resource/        候选构建与版本状态
│   ├── knowledge/       知识库与文档阅读
│   ├── patrol/          巡检与修复
│   ├── admin/           治理、用量、合规
│   ├── settings/        通用、Agent、推理、Skill、记忆、集成、运行时
│   └── ui/              Radix 基础组件
├── hooks/               状态与流式编排
├── lib/api/             强类型 HTTP/SSE 客户端
├── lib/session-events.ts
├── providers/
└── i18n/
messages/                权威中英文词典
scripts/                 严格 i18n 一致性检查
```

主要路由包括 `/sessions/[id]`、`/knowledge`、`/automation`、
`/patrols`、`/patrol-runs/[id]`、`/teams` 与 `/admin/*`。设置包含通用、Agent、
推理、Skill、记忆、集成，以及仅管理员可见的运行时配置。

## 开发

```bash
npm install
npm run i18n:check
npm run typecheck
npm run lint
npm run test
npm run build
```

`messages/en.json` 与 `messages/zh.json` 是唯一翻译事实来源。翻译变更直接同步修改
两份词典；`npm run i18n:check` 会拒绝 locale 错配、缺失、未使用、未登记动态调用和
面向用户的硬编码文本。

API 访问统一使用 `src/lib/api/fetch.ts`；保持 TypeScript strict；业务组件放在对应
领域目录；不要在 `src/lib/api/` 之外硬编码 API 路径。

开发服务器为 `http://localhost:3000`，默认 API 为
`http://localhost:8088/api`；生产通过反向代理使用 `/api`。

参见[前端架构](../docs/architecture/frontend-ui.zh-CN.md)与
[执行内核](../docs/architecture/execution-kernel.zh-CN.md)。
