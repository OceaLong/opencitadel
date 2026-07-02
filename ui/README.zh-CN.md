[English](README.md) · [简体中文](README.zh-CN.md)

# OpenCitadel 前端 UI

基于 Next.js 构建的前端用户界面，提供会话管理、AI 对话、模型与 Skill 选择、长期记忆管理、远程桌面（VNC）等交互功能。

## 技术栈

- Next.js 16 (React 19)
- TypeScript
- Tailwind CSS 4
- Radix UI（组件库）
- noVNC（远程桌面）

## 项目结构

```
ui/
├── src/
│   ├── app/               # 页面路由
│   │   ├── page.tsx       # 首页
│   │   ├── sessions/      # 会话页面
│   │   └── settings/      # 设置中心（模型、Skill、记忆）
│   ├── components/        # 组件
│   │   ├── ui/            # 基础 UI 组件
│   │   └── tool-use/      # 工具使用相关组件
│   ├── lib/
│   │   └── api/           # API 客户端
│   ├── hooks/             # 自定义 Hooks
│   └── providers/         # Context Providers
├── public/                # 静态资源
├── eslint.config.mjs      # ESLint + 导入排序配置
├── .prettierrc            # Prettier 格式化配置
├── next.config.ts         # Next.js 配置
├── Dockerfile
├── package.json
└── tsconfig.json
```

## 主要功能

- **会话配置**：首页和会话详情页支持选择模型与 Skill；会话详情页可在非运行状态下更新配置。
- **流式对话**：SSE 事件流 + Token 级 `message_delta` 增量合并渲染；历史事件通过 `events_next_cursor` 与 `/sessions/{id}/events` 分页补齐。
- **模型管理**：通过「设置中心 → 模型管理」维护多 Provider 模型，支持新增、编辑、删除和设置默认模型。
- **Skill 模板**：通过「设置中心 → Skill 模板」维护系统提示词、可用工具、推荐模型、Agent 参数和示例问题。
- **长期记忆**：通过「设置中心 → 长期记忆」维护全局或会话级记忆；会话详情页可查看、压缩、清空或删除 Agent 内存消息。
- **设置弹窗**：保留通用配置、A2A Agent 配置和 MCP 服务器配置；模型提供商配置已迁移到设置中心。

## API 调用

项目通过环境变量 `NEXT_PUBLIC_API_BASE_URL` 配置 API 地址：

- **开发环境**：默认 `http://localhost:8088/api`（直连 API 服务）
- **生产环境**：构建时设置为 `/api`（通过 Nginx 反向代理）

前端 API 客户端位于 `src/lib/api/`，包含会话、文件、应用配置、模型、Skill 和记忆等模块。

## 本地开发

### 环境准备

- Node.js >= 22
- npm >= 10

### 安装与启动

```bash
npm install
npm run dev
```

开发服务器默认运行在 `http://localhost:3000`，API 默认请求 `http://localhost:8088/api`。

### 代码质量

```bash
npm run lint
npm run lint:fix
npm run typecheck
npm run format
npm run format:check
```

### 构建

```bash
npm run build
npm run start
```

## Docker 部署

UI 服务通过根目录的 `docker-compose.yml` 统一部署。Dockerfile 采用多阶段构建：

1. **deps** — 安装 npm 依赖
2. **builder** — 构建 Next.js 应用（standalone 模式）
3. **runner** — 最小化生产镜像

构建时通过 `NEXT_PUBLIC_API_BASE_URL=/api` 参数将 API 地址设置为相对路径，由 Nginx 统一代理。
