[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel UI

Next.js 前端：会话管理、AI 对话、端点/模型设置、Skill 模板、长期记忆、HITL 门控与远程桌面（VNC）。

## 技术栈

- Next.js 16（React 19，App Router）
- TypeScript
- Tailwind CSS 4
- Radix UI + Lucide 图标
- next-intl 4.x（locale：`en` / `zh`）
- noVNC（远程桌面）、Mermaid（图表）、Recharts（管理后台图表）

## 项目结构

```
ui/
├── src/
│   ├── app/               # App Router 页面
│   │   ├── page.tsx       # 首页（新建会话）
│   │   ├── sessions/      # 会话详情
│   │   ├── codebase/      # 代码知识库
│   │   ├── knowledge/     # 文档知识库
│   │   ├── marketplace/   # 应用市场
│   │   ├── automation/    # 定时任务
│   │   ├── teams/         # 团队工作区
│   │   ├── admin/         # 管理后台
│   │   ├── login/         # 登录
│   │   ├── register/      # 注册
│   │   ├── invitations/   # 接受团队邀请
│   │   └── share/         # 公开交付物分享
│   ├── components/
│   │   ├── settings/      # 设置 Tab（模型、Skill、记忆...）
│   │   ├── ui/            # Radix 基础组件
│   │   └── tool-use/      # 工具执行 UI
│   ├── lib/api/           # API 客户端
│   ├── hooks/
│   ├── providers/
│   └── i18n/
├── scripts/               # i18n 构建/检查
├── messages/              # 生成的 en.json / zh.json
└── public/
```

## 路由

| 路由 | 页面 | Shell |
|------|------|-------|
| `/` | 首页 — 创建会话、选模型/Skill | 侧栏 + 顶栏 |
| `/sessions/[id]` | 流式对话、HITL、VNC、交付物 | 侧栏 + 顶栏 |
| `/codebase`、`/codebase/[id]` | 代码导入与 Ask/Agent | 侧栏 + 顶栏 |
| `/knowledge`、`/knowledge/[id]` | 文档知识库与 RAG | 侧栏 + 顶栏 |
| `/marketplace` | LLM 小应用 | 侧栏 + 顶栏 |
| `/automation` | Cron/Webhook 任务 | 侧栏 + 顶栏 |
| `/teams`、`/teams/[id]` | 团队管理 | 侧栏 + 顶栏 |
| `/login`、`/register` | 认证 | 无 Shell |
| `/admin` | 概览仪表盘（用量图表） | Admin 布局 |
| `/admin/users` | 用户管理 | Admin 布局 |
| `/admin/teams` | 团队管理 | Admin 布局 |
| `/admin/invitations` | 平台邀请 | Admin 布局 |
| `/admin/audit` | 审计日志 | Admin 布局 |
| `/admin/compliance` | 证据中心 | Admin 布局 |
| `/admin/compliance/report` | 合规报告导出 | Admin 布局 |
| `/invitations/[token]` | 接受邀请 | 无 Shell |
| `/share/artifact/[token]` | 公开交付物 | 无 Shell |

**导航**：

- **桌面**：左侧为会话列表；Codebase、Knowledge、Marketplace、Automation 在 **顶栏工作区下拉**（`app-header.tsx`）。
- **移动**：`MobileBottomNav` 提供对话、代码库、知识库、应用市场；Automation、Teams、Settings、Admin 在 **更多** Sheet。
- `/codebase/[id]`、`/knowledge/[id]` 会跳转到新建的 Ask 会话，**不是**独立详情页。

## 功能

- **会话配置**：首页与会话页选择模型与 Skill；空闲时可更新。
- **流式对话**：SSE + `message_delta` 合并；历史经 `/sessions/{id}/events` 回放。
- **端点与模型**：设置 → 模型 — 按端点分组（Provider、Base URL、API Key 在端点上）。
- **Skill 模板**：设置 → Skill。
- **长期记忆**：设置 → 记忆；会话页可压缩/清空会话记忆。
- **设置弹窗**（八 Tab）：通用（主题+语言）、Agent、模型、Skill、记忆、集成（MCP/A2A/服务 Key）、HITL、运行时（仅 admin）。
- **主题与语言**：设置 → 通用（`GeneralSettings`）；locale 存于 `NEXT_LOCALE` Cookie（URL 无 locale 前缀）。
- **HITL UI**：澄清问题、计划/工具审批条、VNC 接管、检查点恢复；全局默认在设置 → HITL。
- **Web Operator**：Skill 为 `web-operator` 时弹出 `operator-scope-dialog.tsx`。
- **模型状态 Badge**：顶栏轮询 `/api/llm/status`。
- **通知收件箱**：顶栏 `NotificationInbox`（REST + SSE）。
- **移动端会话工具栏**：模型/Skill/上下文选项在 `ChatOptionsSheet`。

详见 [`../docs/architecture/frontend-ui.zh-CN.md`](../docs/architecture/frontend-ui.zh-CN.md)。

## 前端开发规范

贡献者应遵循以下约定（Cursor IDE 中亦见 `ui/.cursor/rules.general.mdc`）：

- **TypeScript 严格模式**；优先 `type` 而非 `interface`；路径别名 `@/*` → `./src/*`
- **App Router**：默认服务端组件；仅在需要时加 `"use client"`；页面位于 `src/app/**/page.tsx`
- **组件**：基础 UI 在 `@/components/ui/`（shadcn/Radix）；业务组件在 `@/components/`；使用 `cn()` 与 CVA 定义变体
- **Hooks**：复杂状态/副作用从大型页面下沉到 `src/hooks/`
- **导入顺序**：React/Next → 第三方 → `@/components` → `@/lib`/`@/hooks` → 相对路径
- **格式化**：Prettier（100 列、双引号）；PR 前运行 `npm run format:check`
- **API 客户端**：使用 `src/lib/api/fetch.ts`；勿在 `src/lib/api/` 外硬编码路由

## API 客户端

基础 URL：`NEXT_PUBLIC_API_BASE_URL`

- **开发**：`http://localhost:8088/api`
- **生产**：`/api`（Nginx 反代）

核心：`src/lib/api/fetch.ts` — Cookie 认证、CSRF、`X-Workspace-Id`、401 刷新、SSE。

| 模块 | 用途 |
|------|------|
| `session.ts` | 会话、chat SSE、检查点 |
| `endpoints.ts` | `/llm-endpoints` CRUD |
| `models.ts` | `/llm-models` CRUD |
| `llm-status.ts` | `/llm/status` |
| `config.ts` | AppConfig 分段 |
| `skills.ts`、`memory.ts` | Skill 与记忆 |
| `admin.ts`、`team.ts` | 管理与团队 |
| `knowledge.ts`、`codebase.ts`、`file.ts` | 知识库、代码库、文件 |
| `service-keys.ts`、`notifications.ts`、`compliance.ts` | 服务 API Key、通知、合规 |
| `constants.ts` | 共享限制（`CODEBASE_ZIP_MAX_BYTES` = 200 MB，须与 nginx 一致） |
| `artifacts.ts` | 交付物与分享 |
| `types.ts` | 共享 TypeScript 类型 |

## 本地开发

### 前置

- Node.js >= 22
- npm >= 10

### 安装与运行

```bash
npm install
npm run dev
```

开发服务器：`http://localhost:3000`；API 默认：`http://localhost:8088/api`。

### 代码质量

```bash
npm run lint
npm run lint:fix
npm run typecheck
npm run format
npm run format:check
npm run i18n:check
```

### 构建

```bash
npm run build
npm run start
```

## Docker 部署

通过根目录 `docker-compose.yml` 部署。多阶段 Dockerfile：deps → builder → runner。

构建时使用 `NEXT_PUBLIC_API_BASE_URL=/api`，由 Nginx 反代 API。

## 测试

- **单元测试**（Vitest）：`ui/src/**/*.test.ts` 覆盖逻辑层 — 安全重定向、会话事件、LLM 状态、知识库工具函数。无组件级 UI 回归套件。
- **E2E**（Playwright）：`e2e/` 冒烟测试 — 仅首页加载与 OpsConsole 演示登录。见 [`../e2e/README.zh-CN.md`](../e2e/README.zh-CN.md)。

请勿将 `npm run test` 理解为已覆盖完整 UI 流程。

## 国际化（i18n）

- 框架：`next-intl`，locale `en` / `zh`（默认 `en`）
- **键源（权威）**：`scripts/build-messages.mjs`（+ `i18n-supplement.mjs` 回填漂移）→ `npm run i18n:build` → `messages/en.json`、`zh.json`
- **禁止**只手改 `messages/*.json` 而不同步构建脚本并重新执行 `i18n:build`
- 运行时 locale 为 `zh`；文档文件名用 `*.zh-CN.md` — 同一语言，不同标识
- URL 无 locale 前缀（`localePrefix: "never"`）；locale 存于 `NEXT_LOCALE` Cookie
- 主题与语言：**设置 → 通用**（`GeneralSettings`）
